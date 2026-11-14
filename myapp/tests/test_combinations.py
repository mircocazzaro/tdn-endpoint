"""Segnaposto mappati a una combinazione di colonne (somma o concatenazione).

Il mapping salvato legge da una tabella derivata che espone la combinazione come
colonna virtuale; la UI ritrova modalita' e colonne da un file accanto al mapping.
Le prove con Ontop reale (DuckDB, SQLite, MySQL, PostgreSQL) stanno fuori dalla
suite; qui si controllano generazione, validazione e ripristino.
"""

import json
import tempfile
from pathlib import Path

import duckdb
from django.test import Client, TestCase
from .helpers import logged_in_client

from myapp import combinations as cmb
from myapp import dialects, views
from myapp.dialects import DUCKDB, MSSQL, MYSQL, ORACLE, POSTGRESQL, SQLITE
from myapp.obda_mapping import parse_mappings, unresolved_placeholders

from .hdn_helpers import StateDirMixin

TEMPLATE = '''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-NAME
target		bto:Patient{patient} bto:name {name}^^xsd:string .
source		SELECT patient, LOWER(name) AS name
			FROM "SRC"
			WHERE name <> ''

mappingId	MAPID-AGE
target		bto:Patient{patient} bto:ageOnset {age}^^xsd:float .
source		SELECT patient, age FROM "SRC" WHERE NOT isnan(age)

mappingId	MAPID-NOTE
target		bto:Patient{patient} bto:note {note}^^xsd:string .
source		SELECT patient, note FROM "SRC"
]]
'''

ALL = ["pid", "first", "last", "a", "b", "note"]
TYPES = {"pid": "VARCHAR", "first": "VARCHAR", "last": "VARCHAR",
         "a": "DOUBLE", "b": "DOUBLE", "note": "VARCHAR"}


class DialectExpressionTests(TestCase):

    def test_sum_is_the_same_everywhere(self):
        for d in (DUCKDB, SQLITE, MYSQL, POSTGRESQL, ORACLE, MSSQL):
            self.assertEqual(dialects.sum_expression(d, ["a", "b c"]), '("a" + "b c")')

    def test_concat_is_binary_and_nested(self):
        # Ontop impone CONCAT a due argomenti su SQLite e Oracle: la forma annidata vale ovunque
        for d in (DUCKDB, SQLITE, MYSQL, POSTGRESQL, ORACLE, MSSQL):
            expr = dialects.concat_expression(d, ["a", "b", "c"], "-")
            self.assertEqual(expr.count("CONCAT("), 4, d)
            start = expr.find("CONCAT(")
            while start != -1:
                depth, commas, i = 1, 0, start + len("CONCAT(")
                while depth:
                    ch = expr[i]
                    depth += ch == "("
                    depth -= ch == ")"
                    commas += ch == "," and depth == 1
                    i += 1
                self.assertEqual(commas, 1, f"{d}: CONCAT con piu' di due argomenti in {expr}")
                start = expr.find("CONCAT(", start + 1)

    def test_null_counts_as_empty_text_on_every_engine(self):
        for d in (DUCKDB, MYSQL, POSTGRESQL, ORACLE):
            self.assertIn("COALESCE(", dialects.concat_expression(d, ["a", "b"], ""))

    def test_text_cast_matches_the_engine(self):
        self.assertEqual(dialects.text_cast(MYSQL, "x"), "CAST(x AS CHAR)")
        self.assertEqual(dialects.text_cast(POSTGRESQL, "x"), "CAST(x AS TEXT)")
        self.assertEqual(dialects.text_cast(DUCKDB, "x"), "CAST(x AS VARCHAR)")

    def test_separator_quote_is_doubled_and_empty_separator_is_omitted(self):
        self.assertIn("'it''s'", dialects.concat_expression(DUCKDB, ["a", "b"], "it's"))
        self.assertNotIn("''  ", dialects.concat_expression(DUCKDB, ["a", "b"], ""))
        self.assertEqual(dialects.concat_expression(DUCKDB, ["a", "b"], "").count("CONCAT("), 1)

    def test_no_backslashes_anywhere(self):
        # Ontop raddoppia i backslash nei literal
        for d in (DUCKDB, SQLITE, MYSQL, POSTGRESQL, ORACLE, MSSQL):
            self.assertNotIn("\\", dialects.concat_expression(d, ["a", "b"], ", "))


class ParseTests(TestCase):

    def test_valid_concat_keeps_the_order(self):
        c = cmb.parse({"mode": "concat", "columns": ["last", "first"], "separator": ", "}, ALL)
        self.assertEqual(c, {"mode": "concat", "columns": ["last", "first"], "separator": ", "})

    def test_sum_drops_the_separator(self):
        c = cmb.parse({"mode": "sum", "columns": ["a", "b"], "separator": "-"}, ALL, TYPES)
        self.assertEqual(c["separator"], "")

    def test_rejections(self):
        bad = [
            ({"mode": "avg", "columns": ["a", "b"]}, "unknown mode"),
            ({"mode": "sum", "columns": ["a"]}, "2 to"),
            ({"mode": "sum", "columns": []}, "2 to"),
            ({"mode": "sum", "columns": ["a", "a"]}, "twice"),
            ({"mode": "sum", "columns": ["a", "zzz"]}, "unknown column"),
            ({"mode": "sum", "columns": ["a", "first"]}, "numeric"),
            ({"mode": "concat", "columns": ["a", "b"], "separator": "\\"}, "backslash"),
            ({"mode": "concat", "columns": ["a", "b"], "separator": "x" * 21}, "at most"),
            ({"mode": "concat", "columns": ["a", "b"], "separator": "a\nb"}, "control"),
            ({"mode": "concat", "columns": "ab"}, "2 to"),
        ]
        for value, fragment in bad:
            with self.assertRaises(cmb.CombinationError, msg=str(value)) as cm:
                cmb.parse(value, ALL, TYPES)
            self.assertIn(fragment, str(cm.exception), str(value))

    def test_too_many_columns(self):
        cols = [f"c{i}" for i in range(cmb.MAX_COLUMNS + 1)]
        with self.assertRaises(cmb.CombinationError):
            cmb.parse({"mode": "concat", "columns": cols}, cols)

    def test_numeric_types(self):
        for t in ("DOUBLE", "BIGINT", "INTEGER", "DECIMAL(10,2)", "FLOAT", "HUGEINT", "NUMERIC"):
            self.assertTrue(cmb.is_numeric_type(t), t)
        for t in ("VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN", "INTERVAL", "", None):
            self.assertFalse(cmb.is_numeric_type(t), t)

    def test_virtual_name_never_clashes_with_a_column(self):
        self.assertEqual(cmb.virtual_name("name", {"x"}), "hdn_name")
        self.assertEqual(cmb.virtual_name("name", {"hdn_name"}), "hdn_name_")
        self.assertEqual(cmb.virtual_name("name", {"hdn_name", "hdn_name_"}), "hdn_name__")


class WrapSourceTests(TestCase):

    def test_from_becomes_a_derived_table_with_the_same_alias(self):
        src = 'SELECT patient, hdn_name FROM "SRC" WHERE hdn_name <> \'\''
        out = cmb.wrap_source(src, "SRC", ["pid", "first"], [("hdn_name", '("a" + "b")')])
        self.assertEqual(
            out,
            'SELECT patient, hdn_name FROM (SELECT "pid", "first", ("a" + "b") AS hdn_name '
            'FROM "SRC") AS "SRC" WHERE hdn_name <> \'\'')

    def test_without_virtuals_the_source_is_unchanged(self):
        self.assertEqual(cmb.wrap_source('SELECT x FROM "SRC"', "SRC", ["x"], []), 'SELECT x FROM "SRC"')

    def test_table_names_with_spaces_and_quotes(self):
        out = cmb.wrap_source('SELECT x FROM "PATIENTS GENERAL DATA"', "PATIENTS GENERAL DATA",
                              ["x"], [("hdn_x", "1")])
        self.assertIn('FROM "PATIENTS GENERAL DATA") AS "PATIENTS GENERAL DATA"', out)

    def test_alias_is_not_quoted(self):
        # un alias senza apici si riduce al caso del motore come i riferimenti nudi
        out = cmb.wrap_source('SELECT x FROM "T"', "T", ["x"], [("hdn_x", "1")])
        self.assertIn("1 AS hdn_x", out)
        self.assertNotIn('AS "hdn_x"', out)


class SidecarTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.obda = Path(self._tmp.name) / "site.obda"

    ENTRY = {"table": "SRC", "virtual": "hdn_name", "mode": "concat",
             "columns": ["first", "last"], "separator": " "}

    def test_round_trip_and_removal_when_empty(self):
        cmb.save_sidecar(self.obda, {"M": {"name": self.ENTRY}})
        self.assertEqual(cmb.load_sidecar(self.obda), {"M": {"name": self.ENTRY}})
        self.assertEqual(cmb.sidecar_path(self.obda).name, "site.combinations.json")
        cmb.save_sidecar(self.obda, {})
        self.assertFalse(cmb.sidecar_path(self.obda).exists())

    def test_missing_or_corrupt_file_is_empty(self):
        self.assertEqual(cmb.load_sidecar(self.obda), {})
        cmb.sidecar_path(self.obda).write_text("not json", encoding="utf-8")
        self.assertEqual(cmb.load_sidecar(self.obda), {})
        cmb.sidecar_path(self.obda).write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(cmb.load_sidecar(self.obda), {})

    def line(self, entry=None, dialect=DUCKDB):
        e = entry or self.ENTRY
        expr = cmb.expression(dialect, {"mode": e["mode"], "columns": e["columns"],
                                        "separator": e.get("separator", "")})
        return f'SELECT patient, hdn_name FROM (SELECT "first", {expr} AS hdn_name FROM "SRC") AS "SRC"'

    def test_restored_needs_the_mapping_to_confirm_it(self):
        line = self.line()
        ok = cmb.restored(self.ENTRY, "hdn_name", "SRC", line)
        self.assertEqual(ok, {"mode": "concat", "columns": ["first", "last"], "separator": " "})
        self.assertIsNone(cmb.restored(self.ENTRY, "other_col", "SRC", line), "colonna diversa")
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "OTHER", line), "tabella diversa")
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", 'SELECT patient FROM "SRC"'),
                          "il source non definisce la colonna")
        self.assertIsNone(cmb.restored(None, "hdn_name", "SRC", line))
        self.assertIsNone(cmb.restored(dict(self.ENTRY, columns=["only"]), "hdn_name", "SRC", line))

    def test_restored_requires_the_file_to_compute_what_the_metadata_says(self):
        # un backup del mapping rimesso a mano con colonne o separatore diversi
        other = dict(self.ENTRY, columns=["last", "first"])
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", self.line(other)), "ordine diverso")
        sep = dict(self.ENTRY, separator=", ")
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", self.line(sep)), "separatore diverso")
        summed = dict(self.ENTRY, mode="sum", columns=["a", "b"], separator="")
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", self.line(summed)), "modalita' diversa")

    def test_restored_uses_the_dialect_the_mapping_was_saved_for(self):
        line = self.line(dialect=POSTGRESQL)
        self.assertIsNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", line, DUCKDB))
        self.assertIsNotNone(cmb.restored(self.ENTRY, "hdn_name", "SRC", line, POSTGRESQL))


class CombinationViewTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = logged_in_client()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)

        self.db_path = str(tmp / "src.duckdb")
        con = duckdb.connect(self.db_path)
        con.execute('CREATE TABLE "SRC" (pid VARCHAR, first VARCHAR, last VARCHAR, '
                    'a DOUBLE, b DOUBLE, note VARCHAR)')
        con.execute("""INSERT INTO "SRC" VALUES
            ('p1', 'Ana', 'Rossi', 40.5, 3.5, 'x'),
            ('p2', 'Luca', NULL, 50.0, 2.0, NULL),
            ('p3', NULL, 'Verdi', NULL, 1.0, 'y')""")
        con.close()

        self.template_path = tmp / "template.obda"
        self.template_path.write_text(TEMPLATE, encoding="utf-8")
        self.out_path = tmp / "generated.obda"

        for name, value in [("DUCKDB_PATH", self.db_path),
                            ("TEMPLATE_OBDA", str(self.template_path)),
                            ("OBDA_FILE", str(self.out_path)),
                            ("ONTOP_DIR", str(tmp))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)

    def post(self, **connections):
        data = {}
        for mid, conn in connections.items():
            data[f"MAPID-{mid.upper()}__table"] = "SRC"
            data[f"connections_MAPID-{mid.upper()}"] = json.dumps(conn)
        return self.client.post("/map-fields/", data)

    def blocks(self):
        return {b.mapping_id: b for b in parse_mappings(self.out_path.read_text(encoding="utf-8"))}

    def run_source(self, block):
        con = duckdb.connect(self.db_path, read_only=True)
        try:
            return con.execute(block.source).fetchall()
        finally:
            con.close()

    NAME = {"mode": "concat", "columns": ["first", "last"], "separator": " "}

    def test_concat_is_saved_as_a_derived_table_and_computes_the_text(self):
        r = self.post(name={"patient": "pid", "name": self.NAME})
        self.assertEqual(r.status_code, 302, [str(m) for m in r.context["messages"]] if r.context else "")
        block = self.blocks()["MAPID-NAME"]
        self.assertIn("{hdn_name}", block.target)
        self.assertIn('FROM (SELECT "pid", "first", "last", "a", "b", "note", CONCAT(', block.source)
        self.assertIn(") AS hdn_name FROM \"SRC\") AS \"SRC\"", block.source)
        self.assertEqual(unresolved_placeholders(block.target, block.source), [])
        # LOWER(...) del template e WHERE restano, applicati alla combinazione; il NULL e' vuoto
        self.assertEqual(sorted(self.run_source(block)),
                         [("p1", "ana rossi"), ("p2", "luca "), ("p3", " verdi")])

    def test_sum_goes_through_isnan_and_drops_rows_with_a_missing_part(self):
        r = self.post(age={"patient": "pid", "age": {"mode": "sum", "columns": ["a", "b"]}})
        self.assertEqual(r.status_code, 302)
        block = self.blocks()["MAPID-AGE"]
        self.assertIn("{hdn_age}", block.target)
        self.assertIn("NOT isnan(hdn_age)", block.source)
        self.assertEqual(sorted(self.run_source(block)), [("p1", 44.0), ("p2", 52.0)])

    def test_plain_rules_are_untouched(self):
        self.post(note={"patient": "pid", "note": "note"})
        block = self.blocks()["MAPID-NOTE"]
        self.assertNotIn("hdn_", block.source)
        self.assertNotIn("(SELECT", block.source)
        self.assertIn("{note}", block.target)

    def test_combinations_and_plain_rules_in_the_same_save(self):
        r = self.post(name={"patient": "pid", "name": self.NAME},
                      age={"patient": "pid", "age": {"mode": "sum", "columns": ["a", "b"]}},
                      note={"patient": "pid", "note": "note"})
        self.assertEqual(r.status_code, 302)
        blocks = self.blocks()
        self.assertEqual(set(blocks), {"MAPID-NAME", "MAPID-AGE", "MAPID-NOTE"})
        side = cmb.load_sidecar(self.out_path)
        self.assertEqual(set(side), {"MAPID-NAME", "MAPID-AGE"})
        self.assertEqual(side["MAPID-NAME"]["name"]["virtual"], "hdn_name")
        self.assertEqual(side["MAPID-NAME"]["name"]["columns"], ["first", "last"])

    def rejected(self, value, fragment):
        self.out_path.write_text("PRECEDENTE", encoding="utf-8")
        r = self.post(name={"patient": "pid", "name": value})
        self.assertEqual(r.status_code, 200, "la view ha rediretto: il mapping e' stato salvato")
        self.assertEqual(self.out_path.read_text(encoding="utf-8"), "PRECEDENTE")
        msgs = " | ".join(str(m) for m in r.context["messages"])
        self.assertIn(fragment, msgs)
        self.assertFalse(cmb.sidecar_path(self.out_path).exists())

    def test_invalid_combinations_are_not_saved(self):
        self.rejected({"mode": "sum", "columns": ["a", "first"]}, "numeric")
        self.rejected({"mode": "concat", "columns": ["first", "nope"]}, "unknown column")
        self.rejected({"mode": "concat", "columns": ["first"]}, "2 to")
        self.rejected({"mode": "concat", "columns": ["first", "first"]}, "twice")
        self.rejected({"mode": "concat", "columns": ["first", "last"], "separator": "\\"}, "backslash")
        self.rejected({"mode": "xor", "columns": ["first", "last"]}, "unknown mode")

    def test_combination_is_restored_when_the_page_is_reopened(self):
        self.post(name={"patient": "pid", "name": self.NAME})
        page = self.client.get("/map-fields/")
        pairs = page.context["mapping_connections"]["MAPID-NAME"]["pairs"]
        self.assertEqual(pairs["name"], {"mode": "concat", "columns": ["first", "last"], "separator": " "})
        self.assertEqual(pairs["patient"], "pid")
        # il campo nascosto parte da questo stato
        hidden = json.loads(next(b for b in page.context["mapping_ui"]
                                 if b["mappingId"] == "MAPID-NAME")["connections_json"])
        self.assertEqual(hidden["name"]["columns"], ["first", "last"])

    def test_without_the_sidecar_the_page_still_opens(self):
        self.post(name={"patient": "pid", "name": self.NAME})
        cmb.sidecar_path(self.out_path).unlink()
        page = self.client.get("/map-fields/")
        self.assertEqual(page.status_code, 200)
        pairs = page.context["mapping_connections"]["MAPID-NAME"]["pairs"]
        self.assertNotIn("name", pairs, "senza i metadati la combinazione non si puo' ricostruire")

    def test_a_stale_sidecar_is_ignored(self):
        self.post(name={"patient": "pid", "name": self.NAME})
        side = cmb.load_sidecar(self.out_path)
        side["MAPID-NAME"]["name"]["virtual"] = "hdn_other"
        cmb.save_sidecar(self.out_path, side)
        pairs = self.client.get("/map-fields/").context["mapping_connections"]["MAPID-NAME"]["pairs"]
        self.assertNotIn("name", pairs)

    def test_saving_again_without_combinations_removes_the_metadata(self):
        self.post(name={"patient": "pid", "name": self.NAME})
        self.assertTrue(cmb.sidecar_path(self.out_path).exists())
        self.post(name={"patient": "pid", "name": "first"})
        self.assertFalse(cmb.sidecar_path(self.out_path).exists())
        self.assertNotIn("hdn_", self.blocks()["MAPID-NAME"].source)

    def test_a_restored_combination_is_dropped_if_a_column_disappears(self):
        self.post(name={"patient": "pid", "name": self.NAME})
        con = duckdb.connect(self.db_path)
        con.execute('ALTER TABLE "SRC" DROP COLUMN last')
        con.close()
        pairs = self.client.get("/map-fields/").context["mapping_connections"]["MAPID-NAME"]["pairs"]
        self.assertNotIn("name", pairs)

    def test_get_columns_lists_numeric_columns_for_the_sum(self):
        data = self.client.get("/get-columns/", {"table": "SRC"}).json()
        self.assertEqual(data["columns"], ALL)
        self.assertEqual(data["numeric"], ["a", "b"])

    def test_the_page_has_the_modal_and_the_hint(self):
        page = self.client.get("/map-fields/").content.decode()
        self.assertIn('id="combineModal"', page)
        self.assertIn('id="combine-apply"', page)
        self.assertIn("contextmenu", page)
        self.assertIn("Right-click to combine columns", page)
