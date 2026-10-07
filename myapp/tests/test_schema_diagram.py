"""Audit-test del punto 29: il diagramma dello schema regge nomi qualunque.

La home generava ``erDiagram`` scrivendo nomi di tabella e colonna cosi' come
sono. Con ``PATIENTS GENERAL DATA`` (il nome di tabella del template) o una
colonna ``patient id`` mermaid rifiuta il diagramma e la pagina non mostra
nulla, senza errori.

Il controllo con il parser reale di mermaid gira se HDN_MERMAID_NODE_MODULES
indica una directory node_modules con mermaid@11.4.1 e jsdom; altrimenti si
verifica la grammatica prodotta.
"""

import html
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import duckdb
from django.test import Client, SimpleTestCase, TestCase

from myapp import views
from myapp.schema_diagram import er_diagram

AWKWARD = {
    "PATIENTS GENERAL DATA": ["patient id", "sex", "age-at-onset", "1st visit", "w(kg)"],
    'quote"table': ["it's", "SEX", "sex"],
    "ALS FUNCTIONAL RATING SCALE": ["q1", "q 2", "{brace}"],
}

ENTITY = re.compile(r'^  t\d+\["[^"\n]*"\] \{$')
ATTRIBUTE = re.compile(r"^    string [A-Za-z][A-Za-z0-9_]*$")

PARSE_JS = r"""
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.window = dom.window; globalThis.document = dom.window.document;
const { default: mermaid } = await import('mermaid');
let t = ''; for await (const c of process.stdin) t += c;
try { await mermaid.parse(t); } catch (e) { console.log(String(e.message || e)); process.exit(1); }
"""


class ErDiagramTests(SimpleTestCase):

    def test_grammar(self):
        lines = er_diagram(AWKWARD).splitlines()
        self.assertEqual(lines[0], "erDiagram")
        for line in lines[1:]:
            with self.subTest(line=line):
                self.assertTrue(ENTITY.match(line) or ATTRIBUTE.match(line) or line == "  }",
                                f"riga fuori grammatica: {line!r}")

    def test_real_names_are_kept_as_labels(self):
        text = er_diagram(AWKWARD)
        self.assertIn('["PATIENTS GENERAL DATA"]', text)
        self.assertIn("string patient_id", text)

    def test_attribute_names_are_unique_per_table(self):
        lines = er_diagram({"t": ["SEX", "sex", "s e x", "s-e-x"]}).splitlines()
        attrs = [l.split()[-1].lower() for l in lines if l.startswith("    string")]
        self.assertEqual(len(attrs), len(set(attrs)))

    def test_empty_schema(self):
        self.assertEqual(er_diagram({}), "")

    def test_mermaid_parser_accepts_it(self):
        modules = os.environ.get("HDN_MERMAID_NODE_MODULES")
        node = shutil.which("node")
        if not (modules and node and Path(modules, "mermaid").is_dir()):
            self.skipTest("parser mermaid non disponibile (HDN_MERMAID_NODE_MODULES)")
        with tempfile.TemporaryDirectory(dir=Path(modules).parent) as d:
            script = Path(d) / "parse.mjs"
            script.write_text(PARSE_JS, encoding="utf-8")
            for name, schema in [("nuovo", er_diagram(AWKWARD)),
                                 ("precedente", legacy_diagram(AWKWARD))]:
                r = subprocess.run([node, str(script)], input=schema, capture_output=True,
                                   text=True, timeout=120)
                if name == "nuovo":
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                else:
                    self.assertNotEqual(r.returncode, 0,
                                        "controllo negativo: il formato precedente doveva fallire")


def legacy_diagram(tables_columns):
    """Il testo che il template precedente produceva (al netto dell'indentazione)."""
    out = ["erDiagram"]
    for table, cols in tables_columns.items():
        out.append(f"{table} {{")
        out += [f"string {c}" for c in cols]
        out.append("}")
    return "\n".join(out) + "\n"


class HomeDiagramTests(TestCase):

    def test_home_renders_the_diagram_text(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "s.duckdb")
            con = duckdb.connect(db)
            con.execute('CREATE TABLE "PATIENTS GENERAL DATA" ("patient id" VARCHAR)')
            con.close()
            original = views.DUCKDB_PATH
            views.DUCKDB_PATH = db
            try:
                page = Client().get("/").content.decode()
            finally:
                views.DUCKDB_PATH = original
        block = re.search(r'<pre class="mermaid">(.*?)</pre>', page, re.S)
        self.assertIsNotNone(block, "diagramma assente dalla pagina")
        self.assertEqual(html.unescape(block.group(1)),
                         er_diagram({"PATIENTS GENERAL DATA": ["patient id"]}))
