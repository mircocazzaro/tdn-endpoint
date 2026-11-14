"""Editor SQL/SPARQL: schema per il completamento e logica di evidenziazione/suggerimento."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase
from .helpers import logged_in_client

from myapp import ontology, views

STATIC = Path(__file__).resolve().parents[1] / "static" / "myapp"
BASE = "https://example.org/onto#"
TTL = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <{BASE}> .
<urn:o> a owl:Ontology .
ex:Patient a owl:Class ; rdfs:label "Patient"@en .
ex:hasAge a owl:DatatypeProperty ; rdfs:domain ex:Patient .
"""


def run_node(script):
    """Esegue ``script`` con node, che carica query_language.js, e ne legge il JSON."""
    node = shutil.which("node")
    code = (f"const QL = require({json.dumps(str(STATIC / 'query_language.js'))});"
            f"console.log(JSON.stringify((() => {{ {script} }})()));")
    out = subprocess.run([node, "-e", code], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


class HighlightingAndSuggestionTests(SimpleTestCase):
    """Logica pura, eseguita con node: salta se node non e' installato."""

    def setUp(self):
        if shutil.which("node") is None:
            self.skipTest("node non installato")

    def test_sql_tokens_are_classified(self):
        types = run_node("return QL.tokenize(\"SELECT COUNT(*) FROM t WHERE x = 'a' -- c\", 'sql')"
                         ".filter(t => t.type !== 'ws').map(t => t.type);")
        self.assertEqual(types[:4], ["keyword", "function", "op", "op"])
        self.assertIn("string", types)
        self.assertEqual(types[-1], "comment")

    def test_highlight_escapes_html(self):
        html = run_node("return QL.highlightHtml('SELECT <a>', 'sql');")
        self.assertIn('<span class="hdn-tok-keyword">SELECT</span>', html)
        self.assertIn("&lt;a&gt;", html)

    def test_sql_suggests_tables_after_from(self):
        schema = {"tables": {"patients": ["id", "name"], "visits": ["id"]}}
        labels = run_node("const t = 'SELECT * FROM ';"
                          "return QL.suggest(t, t.length, 'sql', " + json.dumps(schema) + ", false)"
                          ".items.map(i => i.label + '/' + i.kind);")
        self.assertEqual(labels, ["patients/table", "visits/table"])

    def test_sql_suggests_columns_of_the_referenced_table(self):
        schema = {"tables": {"patients": ["id", "name"], "visits": ["id"]}}
        result = run_node("const t = 'SELECT na FROM patients';"
                          "return QL.suggest(t, 9, 'sql', " + json.dumps(schema) + ", false).items"
                          ".map(i => i.label);")
        self.assertEqual(result, ["name"])

    def test_sql_qualified_name_completes_the_column_only(self):
        schema = {"tables": {"patients": ["id", "name"]}}
        result = run_node("const t = 'SELECT patients.n FROM patients';"
                          "const r = QL.suggest(t, 17, 'sql', " + json.dumps(schema) + ", false);"
                          "return {from: r.from, items: r.items.map(i => i.insert)};")
        self.assertEqual(result, {"from": 16, "items": ["name"]})

    def test_sparql_completes_local_names_of_a_prefix(self):
        schema = {"prefixes": {"ex": BASE},
                  "terms": [{"iri": BASE + "Patient", "label": "Patient", "kind": "class"},
                            {"iri": BASE + "hasAge", "label": "has age", "kind": "datatype_property"}]}
        result = run_node("const t = 'SELECT ?s WHERE { ?s ex:';"
                          "return QL.suggest(t, t.length, 'sparql', " + json.dumps(schema) + ", false)"
                          ".items.map(i => i.insert);")
        self.assertEqual(result, ["Patient", "hasAge"])

    def test_sparql_suggests_variables_already_used(self):
        result = run_node("const t = 'SELECT ?s ?o WHERE { ?s ?';"
                          "return QL.suggest(t, t.length, 'sparql', {}, false).items.map(i => i.label);")
        self.assertEqual(result, ["?s", "?o"])

    def test_no_suggestions_for_a_single_letter_unless_asked(self):
        self.assertIsNone(run_node("return QL.suggest('x', 1, 'sql', {}, false);"))
        self.assertIsNotNone(run_node("return QL.suggest('S', 1, 'sql', {}, true);"))


class QueryPageSchemaTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = logged_in_client()

    def test_sql_page_embeds_the_tables_for_completion(self):
        db = Path(tempfile.mkdtemp()) / "site.duckdb"
        self.addCleanup(lambda: shutil.rmtree(db.parent, ignore_errors=True))
        import duckdb
        con = duckdb.connect(str(db))
        con.execute("CREATE TABLE patients (id INTEGER, name VARCHAR)")
        con.close()
        with mock.patch.object(views, "DUCKDB_PATH", str(db)):
            resp = self.client.get("/query/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="sql-schema"')
        self.assertContains(resp, "query_editor.js")
        self.assertContains(resp, "query_tools.css")
        page = resp.content.decode()
        start = page.index('id="sql-schema"')
        payload = page[page.index(">", start) + 1:page.index("</script>", start)]
        self.assertEqual(json.loads(payload)["tables"], {"patients": ["id", "name"]})

    def test_sql_page_works_without_data(self):
        with mock.patch.object(views, "DUCKDB_PATH", str(Path(tempfile.gettempdir()) / "absent.duckdb")):
            resp = self.client.get("/query/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="sql-schema"')

    def test_sparql_page_embeds_prefixes_and_terms(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ttl = Path(tmp.name) / "active.ttl"
        ttl.write_text(TTL, encoding="utf-8")
        with mock.patch.object(ontology, "active_path", lambda: ttl):
            resp = self.client.get("/sparql/")
        self.assertEqual(resp.status_code, 200)
        page = resp.content.decode()
        start = page.index('id="sparql-schema"')
        payload = json.loads(page[page.index(">", start) + 1:page.index("</script>", start)])
        self.assertEqual(payload["prefixes"]["ex"], BASE)
        self.assertIn(BASE + "Patient", {t["iri"] for t in payload["terms"]})

    def test_sparql_page_survives_an_unreadable_ontology(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ttl = Path(tmp.name) / "active.ttl"
        ttl.write_text("not turtle {", encoding="utf-8")
        with mock.patch.object(ontology, "active_path", lambda: ttl):
            resp = self.client.get("/sparql/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="sparql-schema"')
