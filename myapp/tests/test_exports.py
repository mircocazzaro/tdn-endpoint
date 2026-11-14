"""Esportazione dei risultati di query in CSV, Excel e PDF (SQL e SPARQL)."""

import contextlib
import datetime as dt
import io
from unittest import mock

import pandas as pd
from django.test import Client, TestCase
from .helpers import logged_in_client
from openpyxl import load_workbook

from myapp import exports


def _ontop(payload):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return mock.patch("requests.post", return_value=resp)


def _duckdb(df):
    conn = mock.Mock()
    conn.execute.return_value.df.return_value = df

    @contextlib.contextmanager
    def fake_read_connection(path):
        yield conn

    return mock.patch("myapp.datastore.read_connection", fake_read_connection)


class CsvExportTests(TestCase):

    def test_semicolon_separator_and_bom(self):
        data = exports.to_csv(["nome", "città"], [["Ana", "Milano"]])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        text = data.decode("utf-8-sig")
        self.assertEqual(text.splitlines(), ["nome;città", "Ana;Milano"])

    def test_missing_values_are_empty(self):
        data = exports.to_csv(["a", "b", "c"], [[None, float("nan"), pd.NaT]])
        self.assertEqual(data.decode("utf-8-sig").splitlines()[1], ";;")

    def test_formula_like_text_is_neutralised(self):
        data = exports.to_csv(["x"], [["=HYPERLINK()"], ["-3"], ["+cmd"], ["@sum"]])
        lines = data.decode("utf-8-sig").splitlines()
        self.assertEqual(lines[1], "'=HYPERLINK()")
        self.assertEqual(lines[2], "-3")
        self.assertEqual(lines[3], "'+cmd")
        self.assertEqual(lines[4], "'@sum")


class XlsxExportTests(TestCase):

    def test_values_and_header(self):
        data = exports.to_xlsx(["id", "nome"], [[1, "Ana"], [2.5, None]])
        ws = load_workbook(io.BytesIO(data)).active
        self.assertEqual([c.value for c in ws[1]], ["id", "nome"])
        self.assertTrue(ws["A1"].font.bold)
        self.assertEqual(ws["A2"].value, 1)
        self.assertEqual(ws["A3"].value, 2.5)
        self.assertIsNone(ws["B3"].value)

    def test_equal_sign_text_is_not_a_formula(self):
        data = exports.to_xlsx(["x"], [["=1+1"]])
        cell = load_workbook(io.BytesIO(data)).active["A2"]
        self.assertEqual(cell.data_type, "s")
        self.assertEqual(cell.value, "=1+1")

    def test_timezone_aware_datetime_becomes_text(self):
        aware = dt.datetime(2024, 1, 2, 3, 4, tzinfo=dt.timezone.utc)
        data = exports.to_xlsx(["t"], [[aware]])
        self.assertIn("2024-01-02", str(load_workbook(io.BytesIO(data)).active["A2"].value))

    def test_empty_result_still_has_header(self):
        data = exports.to_xlsx(["only"], [])
        ws = load_workbook(io.BytesIO(data)).active
        self.assertEqual(ws.max_row, 1)


class PdfExportTests(TestCase):

    def test_is_a_pdf_and_escapes_markup(self):
        data = exports.to_pdf("SPARQL results", ["x"], [["<b>&amp;</b>"]])
        self.assertTrue(data.startswith(b"%PDF"))

    def test_many_rows_span_pages(self):
        rows = [[str(i), "text " * 20] for i in range(400)]
        data = exports.to_pdf("big", ["n", "t"], rows)
        self.assertGreater(data.count(b"/Type /Page\n"), 1)


class SqlExportViewTests(TestCase):

    def test_csv_download(self):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        with _duckdb(df):
            resp = logged_in_client().post("/query/", {"sql_query": "SELECT * FROM t", "export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], exports.CONTENT_TYPES["csv"])
        self.assertIn("attachment; filename=\"sql-results-", resp["Content-Disposition"])
        self.assertTrue(resp["Content-Disposition"].endswith('.csv"'))
        self.assertIn(b"a;b", resp.content)

    def test_xlsx_and_pdf_download(self):
        df = pd.DataFrame({"a": [1]})
        with _duckdb(df):
            xlsx = logged_in_client().post("/query/", {"sql_query": "q", "export": "xlsx"})
            pdf = logged_in_client().post("/query/", {"sql_query": "q", "export": "pdf"})
        self.assertEqual(xlsx["Content-Type"], exports.CONTENT_TYPES["xlsx"])
        self.assertTrue(xlsx["Content-Disposition"].endswith('.xlsx"'))
        self.assertEqual(pdf["Content-Type"], exports.CONTENT_TYPES["pdf"])
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_error_is_shown_instead_of_a_file(self):
        conn = mock.Mock()
        conn.execute.side_effect = RuntimeError("syntax error")

        @contextlib.contextmanager
        def fake(path):
            yield conn

        with mock.patch("myapp.datastore.read_connection", fake):
            resp = logged_in_client().post("/query/", {"sql_query": "bad", "export": "csv"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("syntax error", resp.context["error"])

    def test_plain_execute_still_renders_page(self):
        with _duckdb(pd.DataFrame({"a": [1]})):
            resp = logged_in_client().post("/query/", {"sql_query": "q"})
        self.assertEqual(resp.context["columns"], ["a"])
        self.assertContains(resp, "Download:")

    def test_unknown_format_is_ignored(self):
        with _duckdb(pd.DataFrame({"a": [1]})):
            resp = logged_in_client().post("/query/", {"sql_query": "q", "export": "exe"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("attachment", resp.get("Content-Disposition", ""))


class SparqlExportViewTests(TestCase):

    PAYLOAD = {"head": {"vars": ["s", "n"]},
               "results": {"bindings": [
                   {"s": {"type": "uri", "value": "http://x/1"},
                    "n": {"type": "literal", "value": "3"}}]}}

    def test_xlsx_download_of_select(self):
        with _ontop(self.PAYLOAD):
            resp = logged_in_client().post("/sparql/", {"sparql_query": "SELECT * {}", "export": "xlsx"})
        self.assertEqual(resp["Content-Type"], exports.CONTENT_TYPES["xlsx"])
        ws = load_workbook(io.BytesIO(resp.content)).active
        self.assertEqual([c.value for c in ws[2]], ["http://x/1", "3"])

    def test_ask_download(self):
        with _ontop({"head": {}, "boolean": True}):
            resp = logged_in_client().post("/sparql/", {"sparql_query": "ASK {}", "export": "csv"})
        self.assertIn(b"boolean", resp.content)
        self.assertIn(b"true", resp.content)

    def test_plain_execute_renders_download_bar(self):
        with _ontop(self.PAYLOAD):
            resp = logged_in_client().post("/sparql/", {"sparql_query": "SELECT * {}"})
        self.assertContains(resp, 'name="export" value="pdf"')
