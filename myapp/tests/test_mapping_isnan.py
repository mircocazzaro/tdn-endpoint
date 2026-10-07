"""Audit-test del punto 12: isnan() del template su colonne che nel sito sono testo.

Il template di mapping filtra con ``NOT isnan(col)``, pensato per colonne
DOUBLE. Al caricamento di un CSV basta una cella non numerica (un codice di
valore mancante come 'u') perche' DuckDB tipizzi l'intera colonna come
VARCHAR, e su VARCHAR isnan() non esiste. Il generatore copiava isnan() cosi'
com'era e salvava il mapping senza provarlo: l'endpoint lo scopriva solo dagli
errori di Ontop, che si propagavano anche a query non legate a quel mapping.

Ora il generatore adatta isnan() al tipo della colonna nel sito e prova ogni
source sul database del sito prima di salvare.

Tutti i test usano dati sintetici e il template del repository.
"""

import json
import tempfile
from pathlib import Path

import duckdb
from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase

from myapp import views
from myapp.obda_mapping import adapt_isnan, parse_mappings, split_collection

TEMPLATE = Path(settings.BASE_DIR) / "myapp" / "mappings" / "template.obda"

# Il blocco del template con due isnan() su colonne diverse, preso dal file.
WEIGHT_BLOCK = "MAPID-325ab5a214d6481a8f99ee90d5bb72f3"

# CSV sintetico con le colonne del template: peso e altezza contengono il
# codice 'u' e vengono quindi tipizzati VARCHAR da read_csv_auto.
CSV = (
    "patient,moreThan10PercentWeightloss,height,weight\n"
    "p1,false,170,70.5\n"
    "p2,true,u,82\n"
    "p3,false,165,u\n"
    "p4,true,u,u\n"
)
EXPECTED = {("p1", 170.0, 70.5), ("p2", None, 82.0), ("p3", 165.0, None)}


def _template_block(mid):
    return next(b for b in parse_mappings(TEMPLATE.read_text(encoding="utf-8"))
                if b.mapping_id == mid)


class AdaptIsnanTests(SimpleTestCase):

    def test_text_columns_are_rewritten(self):
        src = "SELECT patient, weight FROM \"T\" WHERE NOT isnan(weight)"
        out, cols = adapt_isnan(src, {"patient": "VARCHAR", "weight": "VARCHAR"})
        self.assertEqual(cols, ["weight"])
        self.assertNotIn("isnan", out)
        self.assertIn("CASE WHEN regexp_full_match(CAST(weight AS VARCHAR)", out)
        self.assertIn("END AS weight", out)

    def test_numeric_columns_are_left_alone(self):
        src = "SELECT patient, weight FROM \"T\" WHERE NOT isnan(weight)"
        for sql_type in ("DOUBLE", "FLOAT", "BIGINT", "DECIMAL(5,2)"):
            with self.subTest(sql_type=sql_type):
                out, cols = adapt_isnan(src, {"weight": sql_type})
                self.assertEqual((out, cols), (src, []))

    def test_bare_isnan_keeps_its_meaning(self):
        out, _ = adapt_isnan('SELECT a FROM "T" WHERE isnan(a)', {"a": "VARCHAR"})
        self.assertIn("(NOT regexp_full_match(CAST(a AS VARCHAR)", out)

    def test_column_names_match_case_insensitively(self):
        out, cols = adapt_isnan('SELECT Weight FROM "T" WHERE NOT isnan(WEIGHT)',
                                {"weight": "VARCHAR"})
        self.assertEqual(cols, ["WEIGHT"])
        self.assertNotIn("isnan", out)

    def test_avoids_try_cast(self):
        """TRY_CAST e' respinto dal parser SQL di Ontop 5.3: l'endpoint non parte."""
        out, _ = adapt_isnan('SELECT a FROM "T" WHERE NOT isnan(a)', {"a": "VARCHAR"})
        self.assertNotIn("TRY_CAST", out.upper())

    def test_semantics_on_duckdb(self):
        """Su testo: tiene solo i numeri decimali; su DOUBLE equivale a NOT isnan."""
        con = duckdb.connect()
        self.addCleanup(con.close)
        con.execute("CREATE TABLE t (id INT, s VARCHAR, d DOUBLE)")
        con.execute("""INSERT INTO t VALUES
            (1, '70.5', 70.5), (2, 'u', 'nan'), (3, NULL, NULL),
            (4, '', -3), (5, '70,5', 0), (6, '-3', 148)""")
        text_src, _ = adapt_isnan("SELECT id, s FROM t WHERE NOT isnan(s)", {"s": "VARCHAR"})
        rows = con.execute(f"SELECT id, s FROM ({text_src}) ORDER BY id").fetchall()
        self.assertEqual(rows, [(1, 70.5), (6, -3.0)])

        numeric_src, _ = adapt_isnan("SELECT id FROM t WHERE NOT isnan(d)", {"d": "DOUBLE"})
        ids = [r[0] for r in con.execute(f"SELECT id FROM ({numeric_src}) ORDER BY id").fetchall()]
        self.assertEqual(ids, [1, 4, 5, 6])


class GeneratorOnTextColumnsTests(TestCase):
    """La pagina "Map Data to HERO" sul template del repo e un sito sintetico."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)

        csv = tmp / "data.csv"
        csv.write_text(CSV, encoding="utf-8")
        self.db = str(tmp / "site.duckdb")
        con = duckdb.connect(self.db)
        con.execute(f"CREATE TABLE \"PATIENTS GENERAL DATA\" AS SELECT * FROM read_csv_auto('{csv}')")
        types = dict(con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'PATIENTS GENERAL DATA'").fetchall())
        con.close()
        # Precondizione: il CSV produce davvero colonne testuali.
        self.assertEqual(types["weight"], "VARCHAR")
        self.assertEqual(types["height"], "VARCHAR")

        # Template ridotto al solo blocco con isnan(), estratto dal template reale.
        header, _ = split_collection(TEMPLATE.read_text(encoding="utf-8"))
        block = _template_block(WEIGHT_BLOCK)
        self.template = tmp / "template.obda"
        self.template.write_text(
            header + "[MappingDeclaration] @collection [[\n"
            f"mappingId\t{block.mapping_id}\ntarget\t\t{block.target}\n"
            f"source\t\t{block.source}\n]]\n", encoding="utf-8")
        self.out = tmp / "generated.obda"

        for name, value in [("DUCKDB_PATH", self.db),
                            ("TEMPLATE_OBDA", str(self.template)),
                            ("OBDA_FILE", str(self.out)),
                            ("ONTOP_DIR", str(tmp))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)
        self.client = Client()

    def _post(self):
        # Le colonne del sito hanno gli stessi nomi del template: associazione
        # identita', come fa un partner che carica un CSV con quelle colonne.
        page = self.client.get("/map-fields/")
        self.assertEqual(page.status_code, 200)
        return self.client.post("/map-fields/", {
            f"{WEIGHT_BLOCK}__table": "PATIENTS GENERAL DATA",
            f"connections_{WEIGHT_BLOCK}": json.dumps({}),
        })

    def test_generated_mapping_executes_on_the_site_data(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 302, "il mapping non e' stato salvato")

        blocks = parse_mappings(self.out.read_text(encoding="utf-8"))
        self.assertEqual(len(blocks), 1)
        src = blocks[0].source
        self.assertNotIn("isnan", src)

        con = duckdb.connect(self.db, read_only=True)
        self.addCleanup(con.close)
        rows = set(con.execute(
            f"SELECT patient, height, weight FROM ({src})").fetchall())
        self.assertEqual(rows, EXPECTED,
                         "il mapping deve esporre solo valori numerici, il resto come NULL")

    def test_the_partner_is_told_what_was_adapted(self):
        resp = self._post()
        texts = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any("adapted to text columns" in t and "height" in t and "weight" in t
                            for t in texts), texts)

    def test_template_as_is_would_not_execute(self):
        """Controllo negativo: il source del template, senza adattamento, fallisce.

        E' quello che il generatore precedente scriveva nel file.
        """
        con = duckdb.connect(self.db, read_only=True)
        self.addCleanup(con.close)
        with self.assertRaises(duckdb.BinderException):
            con.execute(f"SELECT count(*) FROM ({_template_block(WEIGHT_BLOCK).source})").fetchone()


class GeneratorRefusesBrokenSourcesTests(TestCase):
    """Un source che non esegue sul sito non viene salvato, qualunque ne sia la causa."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.db = str(tmp / "site.duckdb")
        con = duckdb.connect(self.db)
        con.execute('CREATE TABLE "SRC" (pid VARCHAR, code VARCHAR)')
        con.execute("INSERT INTO \"SRC\" VALUES ('p1', 'ABC123')")
        con.close()

        # Errore di conversione a runtime: lo stesso del filtro "GUID = True"
        # del punto 6, che il binder accetta e l'esecuzione rifiuta.
        self.template = tmp / "template.obda"
        self.template.write_text(
            "[PrefixDeclaration]\nbto:\t\thttps://w3id.org/brainteaser/ontology/schema/\n\n"
            "[MappingDeclaration] @collection [[\n"
            "mappingId\tMAPID-BROKEN\n"
            "target\t\tbto:Patient{pid} a bto:Patient . \n"
            "source\t\tSELECT pid FROM \"SRC\" WHERE code = True\n"
            "]]\n", encoding="utf-8")
        self.out = tmp / "generated.obda"
        self.out.write_text("CONTENUTO PRECEDENTE", encoding="utf-8")

        for name, value in [("DUCKDB_PATH", self.db),
                            ("TEMPLATE_OBDA", str(self.template)),
                            ("OBDA_FILE", str(self.out)),
                            ("ONTOP_DIR", str(tmp))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)

    def test_broken_source_is_not_saved(self):
        resp = Client().post("/map-fields/", {
            "MAPID-BROKEN__table": "SRC",
            "connections_MAPID-BROKEN": "{}",
        })
        self.assertEqual(resp.status_code, 200, "il mapping rotto e' stato salvato")
        self.assertEqual(self.out.read_text(encoding="utf-8"), "CONTENUTO PRECEDENTE")
        texts = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("MAPID-BROKEN" in t and "non esegue" in t for t in texts), texts)
