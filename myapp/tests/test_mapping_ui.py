"""Audit-test dei punti 26-27 (e 28 per questa pagina): stato della UI di mapping.

Prima:

- il campo connections_<id> riceveva coppie per indice ({0: 3}) dal click e
  per nome ({"patient": "pid"}) dal ridisegno dei mapping salvati, e il
  server provava a leggere ogni chiave come indice. Ricliccando un
  segnaposto caricato dallo stato salvato si ottenevano due voci per lo
  stesso segnaposto, e vinceva quella che il dict iterava per ultima;
- le associazioni salvate erano posizionali (indice segnaposto -> indice
  colonna) e venivano ridisegnate contro le colonne di qualunque tabella
  selezionata: cambiando tabella le frecce indicavano colonne sbagliate e il
  submit le salvava;
- ogni slide del carousel azzerava lo stato non salvato;
- il valore iniziale del campo era inserito con |safe fra apici singoli;
- markup sbilanciato (15 <div> aperti, 17 chiusi).
"""

import json
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import duckdb
from django.test import Client, SimpleTestCase, TestCase

from myapp import views

JS_TEST = Path(__file__).resolve().parent / "js" / "mapping_state.test.js"
NODE = shutil.which("node")

TEMPLATE = '''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX
target		bto:Patient{patient} bto:sex {sex}^^xsd:string . 
source		SELECT patient, sex FROM "SRC"
]]
'''

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr"}


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> inatteso (aperti: {self.stack[-3:]})")
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
            return
        self.stack.pop()


def unbalanced(html):
    p = _Balance()
    p.feed(html)
    return p.errors + [f"<{t}> mai chiuso" for t in p.stack]


class MappingStateJsTests(SimpleTestCase):

    def test_state_module(self):
        if not NODE:
            self.skipTest("node non disponibile")
        r = subprocess.run([NODE, str(JS_TEST)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class MappingPageTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db = str(tmp / "site.duckdb")
        con = duckdb.connect(self.db)
        con.execute('CREATE TABLE "SRC" (pid VARCHAR, "SEX" VARCHAR)')
        con.execute('CREATE TABLE "OTHER" (x VARCHAR, y VARCHAR, z VARCHAR)')
        con.close()
        (tmp / "template.obda").write_text(TEMPLATE, encoding="utf-8")
        self.out = tmp / "active.obda"
        for name, value in [("DUCKDB_PATH", self.db),
                            ("TEMPLATE_OBDA", str(tmp / "template.obda")),
                            ("OBDA_FILE", str(self.out)),
                            ("ONTOP_DIR", str(tmp))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)
        self.client = Client()

    def _save(self, pairs):
        return self.client.post("/map-fields/", {
            "MAPID-SEX__table": "SRC",
            "connections_MAPID-SEX": json.dumps(pairs),
        })

    def test_pairs_are_saved_by_name(self):
        resp = self._save({"patient": "pid", "sex": "SEX"})
        self.assertEqual(resp.status_code, 302)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("bto:Patient{pid} bto:sex {SEX}", text)

    def test_index_pairs_are_rejected_not_reinterpreted(self):
        """Il formato per indice non deve piu' essere indovinato dal server."""
        resp = self._save({"0": 0, "1": 1})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.out.exists(), "salvato un mapping da coppie per indice")

    def test_pairs_for_another_table_are_rejected(self):
        resp = self._save({"patient": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.out.exists())

    def test_saved_pairs_are_exposed_by_name_with_their_table(self):
        self._save({"patient": "pid", "sex": "SEX"})
        page = self.client.get("/map-fields/")
        saved = page.context["mapping_connections"]["MAPID-SEX"]
        self.assertEqual(saved, {"table": "SRC", "pairs": {"patient": "pid", "sex": "SEX"}})
        hidden = page.context["mapping_ui"][0]["connections_json"]
        self.assertEqual(json.loads(hidden), {"patient": "pid", "sex": "SEX"})

    def test_hidden_value_is_escaped(self):
        """Il valore del campo nascosto non deve poter uscire dall'attributo.

        Prima era inserito con |safe fra apici singoli: un apice nel JSON
        chiudeva l'attributo.
        """
        from django.template.loader import render_to_string
        payload = json.dumps({"patient": "it's \"q\" <b>"})
        html = render_to_string("myapp/mapping.html", {
            "mapping_ui": [{"mappingId": "M", "mappingLabel": "M", "table_field": "",
                            "placeholders": ["patient"], "connections_json": payload}],
            "mapping_connections": {},
            "form": None,
        })
        self.assertNotIn("<b>", html)
        self.assertIn('value="{&quot;patient&quot;: &quot;it&#x27;s', html)

    def test_placeholder_case_variants_are_not_duplicated(self):
        """Una colonna 'SEX' del sito non aggiunge un segnaposto accanto a 'sex'."""
        page = self.client.get("/map-fields/")
        self.assertEqual(page.context["mapping_ui"][0]["placeholders"], ["patient", "sex"])

    def test_page_markup_is_balanced(self):
        html = self.client.get("/map-fields/").content.decode()
        self.assertEqual(unbalanced(html), [])
