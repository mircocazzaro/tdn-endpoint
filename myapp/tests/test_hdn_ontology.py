"""Protocollo di distribuzione dell'ontologia e adeguamento dei mapping."""
import hashlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from myapp import ontology, ontop_process
from myapp.models import Notification

from .hdn_helpers import FakeCentral, StateDirMixin

BTO = "https://w3id.org/brainteaser/ontology/schema/"

OLD_TTL = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix bto: <{BTO}> .
<urn:o> a owl:Ontology .
bto:Patient a owl:Class .
bto:Onset a owl:Class .
bto:sex a owl:DatatypeProperty .
bto:ageOnset a owl:DatatypeProperty .
bto:undergo a owl:ObjectProperty .
bto:name a owl:AnnotationProperty .
"""
# Nuova versione: sparisce bto:ageOnset, compare bto:ageAtOnset
NEW_TTL = OLD_TTL.replace("bto:ageOnset a", "bto:ageAtOnset a")

MAPPING = """[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX
target		bto:Patient{patient} a bto:Patient ; bto:sex {sex}^^xsd:string .
source		SELECT patient, sex FROM "P"

mappingId	MAPID-ONSET
target		bto:Patient{patient} bto:undergo bto:onset{patient} . bto:onset{patient} a bto:Onset ; bto:ageOnset {age}^^xsd:float .
source		SELECT patient, age
			FROM "P"

mappingId	MAPID-LABEL
target		bto:Patient{patient} bto:name "has bto:ageOnset in text"@en .
source		SELECT patient FROM "P"
]]
"""


# Template di mapping minimo, valido per tutte le ontologie dei test (bto:sex
# resta sempre dichiarato; bto:Patient{patient} e' un template di IRI).
MIN_TEMPLATE = """[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX-V2
target		bto:Patient{patient} bto:sex {sex}^^xsd:string .
source		SELECT patient, sex FROM "P"
]]
"""


class FilterTests(SimpleTestCase):
    def test_target_terms_ignore_templates_and_literals(self):
        prefixes = {"bto": BTO, "xsd": "http://www.w3.org/2001/XMLSchema#"}
        terms = ontology.target_terms(
            'bto:Patient{p} a bto:Patient ; bto:label "bto:ageOnset" ; bto:x <urn:full> ; '
            'bto:y <urn:t{p}> ; bto:hasDisease NCIT:{d} .', prefixes)
        self.assertEqual(terms, {BTO + "Patient", BTO + "label", BTO + "x", BTO + "y",
                                 BTO + "hasDisease", "urn:full"})

    def test_only_blocks_using_removed_terms_are_dropped(self):
        new, kept, dropped = ontology.filter_mapping(MAPPING, {BTO + "ageOnset"})
        self.assertEqual(kept, ["MAPID-SEX", "MAPID-LABEL"])
        self.assertEqual(dropped, [("MAPID-ONSET", [BTO + "ageOnset"])])
        from myapp.obda_mapping import parse_mappings
        self.assertEqual([b.mapping_id for b in parse_mappings(new)], kept)
        self.assertIn("xsd:\t\thttp://www.w3.org/2001/XMLSchema#", new)

    def test_all_dropped_gives_none(self):
        new, kept, dropped = ontology.filter_mapping(MAPPING, {BTO + "Patient", BTO + "Onset", BTO + "name"})
        self.assertIsNone(new)
        self.assertEqual(len(dropped), 3)


class OntologyProtocolTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.central = FakeCentral()
        self.central.membership()
        self.client = Client()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.obda_dir = Path(tmp.name)
        base_ttl = self.obda_dir / "base.ttl"
        base_ttl.write_text(OLD_TTL)
        self.obda = self.obda_dir / "active.obda"
        self.obda.write_text(MAPPING)
        for name, value in (("TTL_FILE", base_ttl), ("OBDA_FILE", self.obda)):
            p = mock.patch.object(ontop_process, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.calls = []
        for fn, ret in (("is_running", False), ("stop", True), ("start", 1), ("wait_ready", True)):
            p = mock.patch.object(ontop_process, fn,
                                  lambda *a, _f=fn, _r=ret, **k: self.calls.append(_f) or _r)
            p.start()
            self.addCleanup(p.stop)

    def send(self, ttl, version, digest=None, template=MIN_TEMPLATE):
        payload = {"version": version, "ttl": ttl,
                   "sha256": digest or hashlib.sha256(ttl.encode()).hexdigest()}
        if template is not None:
            payload.update(mapping_template=template,
                           template_sha256=hashlib.sha256(template.encode()).hexdigest())
        return self.central.post(self.client, "/hdn/ontology/", "ontology", payload)

    def test_template_installed_and_offered_by_map_fields(self):
        from myapp import views
        self.assertEqual(ontology.template_path(views.TEMPLATE_OBDA), views.TEMPLATE_OBDA)
        self.assertEqual(self.send(NEW_TTL, 1).status_code, 200)
        path = ontology.template_path(views.TEMPLATE_OBDA)
        self.assertEqual(Path(path).read_text(), MIN_TEMPLATE)
        self.assertEqual(ontology.installed()["template_sha256"],
                         hashlib.sha256(MIN_TEMPLATE.encode()).hexdigest())
        import duckdb
        db = str(self.obda_dir / "site.duckdb")
        duckdb.connect(db).close()
        with mock.patch.object(views, "DUCKDB_PATH", db):
            page = self.client.get("/map-fields/").content.decode()
        self.assertIn("MAPID-SEX-V2", page)
        self.assertNotIn("MAPID-ONSET", page)
        self.assertIn("Mapping template updated: 1 blocks",
                      Notification.objects.get(kind=Notification.ONTOLOGY).body)

    def test_template_is_required_and_must_match_the_ontology(self):
        self.assertEqual(self.send(NEW_TTL, 1, template=None).status_code, 400)
        # MAPPING usa bto:ageOnset, che NEW_TTL non dichiara piu'
        resp = self.send(NEW_TTL, 1, template=MAPPING)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("does not declare", json.loads(resp.content)["error"])
        self.assertIn(BTO + "ageOnset", json.loads(resp.content)["error"])
        dup = MIN_TEMPLATE.replace("]]", "\nmappingId\tMAPID-SEX-V2\ntarget\t\tbto:Patient{p} bto:sex {s} .\n"
                                         "source\t\tSELECT p, s FROM \"P\"\n]]")
        self.assertEqual(self.send(NEW_TTL, 1, template=dup).status_code, 422)
        self.assertEqual(self.send(NEW_TTL, 1, template="not a template").status_code, 422)
        # nulla e' cambiato
        self.assertEqual(ontology.installed()["version"], 0)
        self.assertEqual(self.obda.read_text(), MAPPING)
        self.assertFalse((Path(self._state.name) / "ontology" / "template.obda").exists())

    def test_same_ontology_new_template_is_a_new_version(self):
        self.assertEqual(self.send(NEW_TTL, 1).status_code, 200)
        other = MIN_TEMPLATE.replace("MAPID-SEX-V2", "MAPID-SEX-V3")
        same = self.send(NEW_TTL, 1, template=other)
        self.assertEqual(same.status_code, 409)  # stessa versione, contenuto diverso
        self.assertEqual(self.send(NEW_TTL, 2, template=other).status_code, 200)
        self.assertTrue((Path(self._state.name) / "ontology" / "backups" / "v1.template.obda").is_file())

    def test_install_filters_mapping_backs_up_and_notifies(self):
        resp = self.send(NEW_TTL, 1)
        self.assertEqual(resp.status_code, 200)
        self.central.verify(resp, "ontology")
        self.assertEqual(json.loads(resp.content),
                         {"status": "installed", "version": 1, "mapping": "reduced", "kept": 2, "dropped": 1})
        self.assertEqual(ontology.active_path().read_text(), NEW_TTL)
        cmd = ontop_process.command()
        self.assertEqual(cmd[cmd.index("-t") + 1], str(ontology.active_path()))
        self.assertNotEqual(ontology.active_path(), ontop_process.TTL_FILE)
        self.assertNotIn("MAPID-ONSET", self.obda.read_text())
        backups = list((self.obda_dir / "mapping-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertIn("MAPID-ONSET", backups[0].read_text())
        n = Notification.objects.get(kind=Notification.ONTOLOGY)
        self.assertEqual(n.level, Notification.WARNING)
        self.assertIn("New ontology v1 from Fake Central", n.title)
        self.assertIn("MAPID-ONSET", n.body)
        self.assertIn("1 mappings removed", n.body)

    def test_running_ontop_restarted_in_background(self):
        started = []

        class SyncThread:  # il riavvio vero gira in un thread; qui lo si esegue in linea
            def __init__(self, target, args, daemon):
                started.append(daemon)
                self.run = lambda: target(*args)

            def start(self):
                self.run()

        with mock.patch.object(ontop_process, "is_running", lambda: True), \
                mock.patch("threading.Thread", SyncThread):
            self.assertEqual(self.send(NEW_TTL, 1).status_code, 200)
        self.assertEqual(started, [True])
        self.assertEqual(self.calls, ["stop", "start", "wait_ready"])
        self.assertTrue(Notification.objects.filter(title="Ontop restarted with ontology v1").exists())

    def test_every_mapping_invalid_removes_file_and_does_not_start(self):
        ttl = (OLD_TTL.replace("bto:Patient a owl:Class .", "").replace("bto:Onset a owl:Class .", "")
               .replace("bto:name a owl:AnnotationProperty .", ""))
        resp = self.send(ttl, 1)
        self.assertEqual(json.loads(resp.content)["mapping"], "emptied")
        self.assertFalse(self.obda.exists())
        self.assertIn("map the data again", Notification.objects.get().body)

    def test_refusals(self):
        bad_import = NEW_TTL + "<urn:o> owl:imports <http://example.org/online.owl> .\n"
        cases = [
            (self.send(NEW_TTL, 1, digest="0" * 64), 400),
            (self.send("not turtle {{{", 1), 422),
            (self.send(bad_import, 1), 422),
            (self.send(NEW_TTL, 0), 422),
        ]
        for resp, status in cases:
            self.assertEqual(resp.status_code, status)
        self.assertEqual(ontology.installed()["version"], 0)
        self.assertEqual(self.obda.read_text(), MAPPING)
        self.assertIn("not available offline", Notification.objects.filter(
            level=Notification.ERROR).first().body + "".join(
            Notification.objects.values_list("body", flat=True)))

    def test_versions(self):
        self.assertEqual(self.send(NEW_TTL, 2).status_code, 200)
        again = self.send(NEW_TTL, 2)
        self.assertEqual(json.loads(again.content)["status"], "current")
        old = self.send(OLD_TTL, 1)
        self.assertEqual((old.status_code, json.loads(old.content)["installed"]), (409, 2))
        self.assertEqual(self.send(OLD_TTL, 3).status_code, 200)
        self.assertTrue((Path(self._state.name) / "ontology" / "backups" / "v2.ttl").is_file())


class RealOntologyTests(SimpleTestCase):
    def test_repository_ontology_declares_mapped_terms_and_imports_offline(self):
        g = ontology.parse(Path(ontop_process.TTL_FILE).read_text(encoding="utf-8"))
        self.assertLessEqual(ontology.imports(g), ontology.offline_imports())
        # Il template di mapping filtrato contro l'ontologia del repository non perde blocchi
        tpl = (Path(__file__).resolve().parents[1] / "mappings" / "template.obda").read_text()
        none_removed = ontology.filter_mapping(tpl, set())
        self.assertEqual(none_removed[2], [])
        self.assertGreater(len(none_removed[1]), 20)
