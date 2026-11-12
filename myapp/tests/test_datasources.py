"""Menu "Data source": configurazione delle sorgenti (fase 1).

I motori remoti non sono raggiunti: il test di connessione e' simulato. Il
test di SQLite e DuckDB usa file reali.
"""
import json
import os
import sqlite3
import stat
import tempfile
from unittest import mock

import duckdb
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from myapp import datasources as ds

from .hdn_helpers import StateDirMixin


def _sqlite_bytes(tables=("pazienti", "visite")):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    for t in tables:
        con.execute(f"CREATE TABLE {t} (id INTEGER)")
    con.commit()
    con.close()
    data = open(path, "rb").read()
    os.unlink(path)
    return data


def _duckdb_bytes(tables=("esordio",)):
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    con = duckdb.connect(path)
    for t in tables:
        con.execute(f'CREATE TABLE "{t}" (id INTEGER)')
    con.close()
    data = open(path, "rb").read()
    os.unlink(path)
    return data


class RegistryTests(StateDirMixin, TestCase):

    def test_password_is_kept_when_left_blank_and_file_is_private(self):
        ds.save_remote("mysql", {"host": "h", "port": "3306", "database": "d", "user": "u",
                                 "password": "PW-1"})
        ds.save_remote("mysql", {"host": "h2", "port": "3306", "database": "d", "user": "u",
                                 "password": ""})
        self.assertEqual(ds.stored("mysql")["password"], "PW-1")
        self.assertEqual(ds.stored("mysql")["host"], "h2")
        self.assertEqual(stat.S_IMODE(os.stat(ds._registry_path()).st_mode), 0o600)

    def test_public_view_never_exposes_the_password(self):
        ds.save_remote("postgresql", {"host": "h", "port": "5432", "database": "d", "user": "u",
                                      "password": "TOPSECRET"})
        pub = ds.public("postgresql")
        self.assertTrue(pub["has_password"])
        self.assertNotIn("TOPSECRET", json.dumps(pub))

    def test_missing_required_fields(self):
        self.assertEqual(ds.missing("oracle", {"host": "h", "database": "", "user": "u"}),
                         ["Database", "Password"])

    def test_aurora_engine_is_validated(self):
        ds.save_remote("aurora", {"host": "h", "database": "d", "user": "u", "password": "p",
                                  "engine": "postgresql"})
        self.assertEqual(ds.public("aurora")["engine"], "postgresql")
        ds.save_remote("aurora", {"host": "h", "database": "d", "user": "u", "password": "p",
                                  "engine": "oracle"})
        self.assertEqual(ds.public("aurora")["engine"], "mysql")

    def test_password_is_scrubbed_from_driver_errors(self):
        ds.save_remote("mysql", {"host": "h", "database": "d", "user": "u", "password": "S3CRET-PW"})
        with mock.patch("pymysql.connect",
                        side_effect=RuntimeError("login failed for S3CRET-PW")):
            with self.assertRaises(ds.DataSourceError) as cm:
                ds.list_tables("mysql")
        self.assertEqual(cm.exception.code, "unreachable")
        self.assertNotIn("S3CRET-PW", cm.exception.detail)
        self.assertIn(ds.REDACTED, cm.exception.detail)


class FileTests(StateDirMixin, TestCase):

    def test_header_check(self):
        self.assertTrue(ds.check_file_header("sqlite", _sqlite_bytes()[:16]))
        self.assertFalse(ds.check_file_header("sqlite", b"not a database!!"))
        self.assertTrue(ds.check_file_header("duckdb", _duckdb_bytes()[:16]))
        self.assertFalse(ds.check_file_header("duckdb", _sqlite_bytes()[:16]))

    def test_sqlite_tables_are_read_read_only(self):
        ds.store_file("sqlite", [_sqlite_bytes()])
        self.assertEqual(ds.list_tables("sqlite"), ["pazienti", "visite"])

    def test_duckdb_tables_are_read(self):
        ds.store_file("duckdb", [_duckdb_bytes()])
        self.assertEqual(ds.list_tables("duckdb"), ["esordio"])

    def test_no_file_means_not_configured(self):
        with self.assertRaises(ds.DataSourceError) as cm:
            ds.list_tables("sqlite")
        self.assertEqual(cm.exception.code, "not-configured")


class PageTests(StateDirMixin, TestCase):

    def test_page_lists_every_source_with_its_logo(self):
        page = Client().get("/data/").content.decode()
        for label in ("CSV files", "SQLite file", "DuckDB file", "MySQL", "PostgreSQL", "Oracle",
                      "MS SQL Server", "Amazon Aurora"):
            self.assertIn(label, page)
        self.assertIn("myapp/sources/mysql.svg", page)
        self.assertIn("myapp/sources/aurora.svg", page)

    def test_sidebar_entry_is_data_source(self):
        page = Client().get("/data/").content.decode()
        self.assertIn('href="/data/"', page)
        self.assertIn("Data source", page)

    def test_sqlite_upload_is_stored_and_reported(self):
        f = SimpleUploadedFile("db.sqlite", _sqlite_bytes(), content_type="application/octet-stream")
        r = Client().post("/data/", {"kind": "sqlite", "file": f}, follow=True)
        self.assertContains(r, "SQLite file loaded: 2 tables")
        self.assertTrue(ds.file_path("sqlite").exists())

    def test_wrong_file_type_is_rejected(self):
        f = SimpleUploadedFile("x.sqlite", b"plain text", content_type="text/plain")
        r = Client().post("/data/", {"kind": "sqlite", "file": f}, follow=True)
        self.assertContains(r, "not a SQLite file database")
        self.assertFalse(ds.file_path("sqlite").exists())

    def test_remote_save_then_test_uses_the_saved_values(self):
        values = {"kind": "mysql", "action": "test", "host": "db.example", "port": "3306",
                  "database": "studi", "user": "ana", "password": "PW"}
        con = mock.MagicMock()
        con.cursor.return_value.fetchall.return_value = [("esami",)]
        with mock.patch("pymysql.connect", return_value=con) as connect:
            r = Client().post("/data/", values, follow=True)
        self.assertContains(r, "MySQL reachable: 1 tables (esami)")
        self.assertEqual(connect.call_args.kwargs["password"], "PW")
        self.assertNotIn("PW", Client().get("/data/").content.decode())

    def test_aurora_saves_the_chosen_engine(self):
        values = {"kind": "aurora", "action": "save", "host": "h", "port": "", "database": "d",
                  "user": "u", "password": "p", "engine": "postgresql"}
        Client().post("/data/", values)
        self.assertEqual(ds.stored("aurora")["engine"], "postgresql")

    def test_missing_driver_is_reported(self):
        ds.save_remote("oracle", {"host": "h", "port": "1521", "database": "svc", "user": "u",
                                  "password": "p"})
        with mock.patch.dict("sys.modules", {"oracledb": None}):
            with self.assertRaises(ds.DataSourceError) as cm:
                ds.list_tables("oracle")
        self.assertEqual(cm.exception.code, "driver-missing")


def _sqlite_with_rows(n):
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"n{i}") for i in range(n)])
    con.commit()
    con.close()
    data = open(path, "rb").read()
    os.unlink(path)
    return data


class QueryTests(StateDirMixin, TestCase):

    def test_select_on_sqlite_file_returns_columns_and_rows(self):
        ds.store_file("sqlite", [_sqlite_bytes()])
        ds.store_file("sqlite", [_sqlite_with_rows(3)])
        cols, rows, truncated = ds.run_query("sqlite", "SELECT id, name FROM t ORDER BY id")
        self.assertEqual(cols, ["id", "name"])
        self.assertEqual(rows, [[0, "n0"], [1, "n1"], [2, "n2"]])
        self.assertFalse(truncated)

    def test_sqlite_file_is_opened_read_only(self):
        ds.store_file("sqlite", [_sqlite_with_rows(1)])
        with self.assertRaises(ds.DataSourceError) as cm:
            ds.run_query("sqlite", "INSERT INTO t VALUES (9, 'x')")
        self.assertEqual(cm.exception.code, "query-error")

    def test_rows_are_capped(self):
        ds.store_file("sqlite", [_sqlite_with_rows(ds.MAX_ROWS + 10)])
        _, rows, truncated = ds.run_query("sqlite", "SELECT id FROM t")
        self.assertEqual(len(rows), ds.MAX_ROWS)
        self.assertTrue(truncated)

    def test_duckdb_file_query(self):
        ds.store_file("duckdb", [_duckdb_bytes()])
        cols, rows, _ = ds.run_query("duckdb", "SELECT 1 AS one")
        self.assertEqual((cols, rows), (["one"], [[1]]))

    def test_remote_query_uses_a_read_only_connection_and_hides_the_password(self):
        ds.save_remote("postgresql", {"host": "h", "port": "5432", "database": "d", "user": "u",
                                      "password": "PW-SECRET"})
        cur = mock.Mock(description=[("n",)])
        cur.fetchmany.return_value = [(7,)]
        con = mock.Mock()
        con.cursor.return_value = cur
        with mock.patch("myapp.datasources._connect", return_value=("postgresql", con)):
            cols, rows, _ = ds.run_query("postgresql", "SELECT 7 AS n")
        self.assertEqual((cols, rows), (["n"], [[7]]))
        con.close.assert_called_once()

        cur.execute.side_effect = RuntimeError("syntax error near PW-SECRET")
        with mock.patch("myapp.datasources._connect", return_value=("postgresql", con)):
            with self.assertRaises(ds.DataSourceError) as cm:
                ds.run_query("postgresql", "SELEC")
        self.assertNotIn("PW-SECRET", cm.exception.detail)

    def test_only_a_configured_source_can_be_activated(self):
        self.assertEqual(ds.active(), "csv")
        with self.assertRaises(ds.DataSourceError):
            ds.set_active("mysql")
        ds.store_file("sqlite", [_sqlite_bytes()])
        ds.set_active("sqlite")
        self.assertEqual(ds.active(), "sqlite")

    def test_activate_button_switches_the_source(self):
        ds.store_file("sqlite", [_sqlite_bytes()])
        r = Client().post("/data/", {"kind": "sqlite", "action": "activate"}, follow=True)
        self.assertContains(r, "is now the source for Run Relational Queries")
        self.assertEqual(ds.active(), "sqlite")

    def test_sql_page_runs_on_the_active_file_source(self):
        ds.store_file("sqlite", [_sqlite_with_rows(2)])
        ds.set_active("sqlite")
        r = Client().post("/query/", {"sql_query": "SELECT id, name FROM t ORDER BY id"})
        self.assertEqual(r.context["columns"], ["id", "name"])
        self.assertEqual(r.context["results"], [[0, "n0"], [1, "n1"]])
        self.assertEqual(r.context["source_label"], "SQLite file")
        self.assertContains(r, "Source: <strong>SQLite file</strong>")

    def test_sql_page_reports_query_errors_from_the_source(self):
        ds.store_file("sqlite", [_sqlite_with_rows(1)])
        ds.set_active("sqlite")
        r = Client().post("/query/", {"sql_query": "SELECT nope FROM t"})
        self.assertIn("no such column", r.context["error"])

    def test_export_works_on_the_active_source(self):
        ds.store_file("sqlite", [_sqlite_with_rows(2)])
        ds.set_active("sqlite")
        r = Client().post("/query/", {"sql_query": "SELECT id FROM t", "export": "csv"})
        self.assertIn(b"id", r.content)
        self.assertEqual(r["Content-Type"].split(";")[0], "text/csv")
