"""Mapping salvati con predicati tolti dal sito, e regole associate solo in parte.

1. Ricaricando un mapping in cui il sito ha tolto un predicato del template,
   le associazioni venivano ricostruite per posizione e scorrevano di un
   posto (bulbar -> data di esordio, axial -> bulbar, ...): ripresentato dalla
   pagina, il mapping reale non si poteva piu' salvare.
2. Una regola con segnaposto senza colonna bloccava il salvataggio di tutto
   il mapping. Ora i predicati senza dato vengono tolti; le regole che non si
   possono ridurre non vengono scritte, e le altre si salvano.
"""
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.test import Client, SimpleTestCase, TestCase

from myapp import ontop_process, views
from myapp.obda_mapping import align_target_placeholders, parse_mappings, prune_unbound

ONSET_TPL = ("bto:Patient{patient} bto:undergo bto:eventOnset1{patient} . bto:eventOnset1{patient} a bto:Onset ; "
             "bto:eventStart {onsetDate}^^xsd:datetime ; bto:bulbarOnset {onset_bulbar}^^xsd:boolean ; "
             "bto:axialOnset {onset_axial}^^xsd:boolean ; bto:limbsOnset {onset_limbs}^^xsd:boolean .")
ONSET_SRC = 'SELECT patient, onsetDate, onset_bulbar, onset_axial, onset_limbs FROM "PATIENTS GENERAL DATA"'
# come nel mapping AnswerALS: eventStart tolto
ONSET_SAVED = ("bto:Patient{Participant_ID} bto:undergo bto:eventOnset1{Participant_ID} . "
               "bto:eventOnset1{Participant_ID} a bto:Onset ; bto:bulbarOnset {Bulbar_Onset}^^xsd:boolean ; "
               "bto:axialOnset {Axial_Onset}^^xsd:boolean ; bto:limbsOnset {Limb_Onset}^^xsd:boolean . ")


class StructureTests(SimpleTestCase):
    def test_alignment_by_structure_not_position(self):
        self.assertEqual(align_target_placeholders(ONSET_TPL, ONSET_SAVED), {
            "patient": "Participant_ID", "onset_bulbar": "Bulbar_Onset",
            "onset_axial": "Axial_Onset", "onset_limbs": "Limb_Onset"})

    def test_prune_drops_predicates_and_projection(self):
        tgt, src, dropped = prune_unbound(ONSET_TPL, ONSET_SRC, ["onsetDate"])
        self.assertEqual(dropped, ["bto:eventStart"])
        self.assertNotIn("onsetDate", tgt + src)
        self.assertIn("bto:eventOnset1{patient} a bto:Onset ; bto:bulbarOnset", tgt)
        self.assertEqual(src, 'SELECT patient, onset_bulbar, onset_axial, onset_limbs FROM "PATIENTS GENERAL DATA"')

    def test_prune_refuses_when_not_reducible(self):
        self.assertIsNone(prune_unbound(ONSET_TPL, ONSET_SRC, ["patient"]))        # soggetto
        self.assertIsNone(prune_unbound("bto:Patient{p} bto:alive {alive} .",
                                        'SELECT p, alive FROM "T" WHERE alive IS NOT NULL',
                                        ["alive"]))                                  # filtro + nulla resta
        self.assertIsNone(prune_unbound("bto:Patient{p} bto:x {a} ; bto:y {b} .",
                                        'SELECT p, a, b FROM "T" WHERE b > 0', ["b"]))  # filtro

    def test_literals_and_iris_are_not_split(self):
        tgt, _, dropped = prune_unbound(
            'bto:P{p} rdfs:label "a ; b . c"@en ; bto:x <http://e.org/a.b{q}> ; bto:y {z} .',
            'SELECT p, q, z FROM "T"', ["z"])
        self.assertEqual(dropped, ["bto:y"])
        self.assertIn('"a ; b . c"@en', tgt)
        self.assertIn("<http://e.org/a.b{q}>", tgt)


TEMPLATE = f'''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX
target		bto:Patient{{patient}} a bto:Patient ; bto:sex {{sex}}^^xsd:string .
source		SELECT patient, sex FROM "PATIENTS GENERAL DATA"

mappingId	MAPID-ONSET
target		{ONSET_TPL}
source		{ONSET_SRC}

mappingId	MAPID-DEATH
target		bto:Patient{{patient}} bto:deathDate {{death}}^^xsd:date .
source		SELECT patient, death FROM "PATIENTS GENERAL DATA" WHERE death IS NOT NULL
]]
'''


class ViewTests(TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.db = str(tmp / "site.duckdb")
        con = duckdb.connect(self.db)
        con.execute("CREATE TABLE t (Participant_ID VARCHAR, Sex VARCHAR, Bulbar_Onset BOOLEAN, "
                    "Axial_Onset BOOLEAN, Limb_Onset BOOLEAN)")
        con.execute("INSERT INTO t VALUES ('p1', 'female', true, false, false)")
        con.close()
        (tmp / "template.obda").write_text(TEMPLATE)
        self.out = tmp / "active.obda"
        for name, value in [("DUCKDB_PATH", self.db), ("TEMPLATE_OBDA", str(tmp / "template.obda")),
                            ("OBDA_FILE", str(self.out)), ("ONTOP_DIR", str(tmp))]:
            p = mock.patch.object(views, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ontop_process, "is_running", lambda: False)
        p.start()
        self.addCleanup(p.stop)
        self.c = Client()

    def saved(self):
        page = self.c.get("/map-fields/").content.decode()
        return json.loads(re.search(r'id="saved-mappings"[^>]*>(.*?)</script>', page, re.S).group(1))

    def save(self, rules):
        post = {}
        for mid, (table, pairs) in rules.items():
            post[f"{mid}__table"] = table
            post[f"connections_{mid}"] = json.dumps(pairs)
        return self.c.post("/map-fields/", post, follow=True)

    def test_partial_rules_are_reduced_or_skipped_and_the_rest_saved(self):
        r = self.save({
            "MAPID-SEX": ("t", {"patient": "Participant_ID", "sex": "Sex"}),
            "MAPID-ONSET": ("t", {"patient": "Participant_ID", "onset_bulbar": "Bulbar_Onset",
                                  "onset_axial": "Axial_Onset", "onset_limbs": "Limb_Onset"}),
            "MAPID-DEATH": ("t", {"patient": "Participant_ID"}),
        })
        msgs = [str(m) for m in r.context["messages"]]
        self.assertTrue(any("MAPID-ONSET: bto:eventStart" in m for m in msgs), msgs)
        self.assertTrue(any("MAPID-DEATH (no column for death)" in m for m in msgs), msgs)
        blocks = {b.mapping_id: b for b in parse_mappings(self.out.read_text())}
        self.assertEqual(set(blocks), {"MAPID-SEX", "MAPID-ONSET"})
        self.assertNotIn("eventStart", blocks["MAPID-ONSET"].target)
        self.assertEqual(blocks["MAPID-ONSET"].source,
                         'SELECT Participant_ID, Bulbar_Onset, Axial_Onset, Limb_Onset FROM "t"')

    def test_reduced_rule_round_trips(self):
        rules = {"MAPID-ONSET": ("t", {"patient": "Participant_ID", "onset_bulbar": "Bulbar_Onset",
                                       "onset_axial": "Axial_Onset", "onset_limbs": "Limb_Onset"})}
        self.save(rules)
        first = self.out.read_text()
        self.assertEqual(self.saved()["MAPID-ONSET"], {"table": "t", "pairs": rules["MAPID-ONSET"][1]})
        saved = self.saved()
        self.save({m: (v["table"], v["pairs"]) for m, v in saved.items() if v["table"]})
        self.assertEqual(self.out.read_text().split("[MappingDeclaration]")[1],
                         first.split("[MappingDeclaration]")[1])

    def test_nothing_writable_keeps_the_file(self):
        self.out.write_text("OLD")
        r = self.save({"MAPID-DEATH": ("t", {"patient": "Participant_ID"})})
        msgs = [str(m) for m in r.context["messages"]]
        self.assertTrue(any("No rule could be written" in m for m in msgs), msgs)
        self.assertEqual(self.out.read_text(), "OLD")


class AnswerALSRoundTripTests(TestCase):
    """Il mapping reale AnswerALS (dati locali, non nel repository) si ricarica e si risalva."""

    DIR = Path(__file__).resolve().parents[2] / "local-testdata" / "answerals"

    def setUp(self):
        if not (self.DIR / "mydatabase.duckdb").exists():
            self.skipTest("local-testdata/answerals non presente")
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "site.duckdb"
        db.write_bytes((self.DIR / "mydatabase.duckdb").read_bytes())
        self.out = tmp / "active.obda"
        self.out.write_text((self.DIR / "hereditary_ontology_2.obda").read_text())
        for name, value in [("DUCKDB_PATH", str(db)), ("OBDA_FILE", str(self.out)), ("ONTOP_DIR", str(tmp))]:
            p = mock.patch.object(views, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ontop_process, "is_running", lambda: False)
        p.start()
        self.addCleanup(p.stop)

    def test_reload_and_save_reproduces_the_site_mapping(self):
        c = Client()
        page = c.get("/map-fields/").content.decode()
        saved = json.loads(re.search(r'id="saved-mappings"[^>]*>(.*?)</script>', page, re.S).group(1))
        onset = saved["MAPID-51a6eb8de7c64b7bbe4fe62d8188208c"]["pairs"]
        self.assertEqual(onset["onset_bulbar"], "Bulbar_Onset")
        self.assertEqual(onset["onset_limbs"], "Limb_Onset")
        self.assertNotIn("onsetDate", onset)
        before = {b.mapping_id: (b.target.split(), b.source.split()) for b in parse_mappings(self.out.read_text())}
        post = {}
        for mid, v in saved.items():
            if v["table"]:
                post[f"{mid}__table"] = v["table"]
                post[f"connections_{mid}"] = json.dumps(v["pairs"])
        r = c.post("/map-fields/", post, follow=True)
        msgs = [str(m) for m in r.context["messages"]]
        self.assertTrue(any("Mappings definition stored" in m for m in msgs), msgs)
        after = {b.mapping_id: (b.target.split(), b.source.split()) for b in parse_mappings(self.out.read_text())}
        self.assertEqual(set(after), set(before))
        for mid in before:
            self.assertEqual(sorted(after[mid][0]), sorted(before[mid][0]), mid)
