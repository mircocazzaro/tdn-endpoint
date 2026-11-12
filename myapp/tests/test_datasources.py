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
        with mock.patch("myapp.datasources._list_mysql",
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
        with mock.patch("myapp.datasources._list_mysql", return_value=["esami"]) as lister:
            r = Client().post("/data/", values, follow=True)
        self.assertContains(r, "MySQL reachable: 1 tables (esami)")
        self.assertEqual(lister.call_args.args[0]["password"], "PW")
        self.assertNotIn("PW", Client().get("/data/").content.decode())

    def test_aurora_saves_the_chosen_engine(self):
        values = {"kind": "aurora", "action": "save", "host": "h", "port": "", "database": "d",
                  "user": "u", "password": "p", "engine": "postgresql"}
        Client().post("/data/", values)
        self.assertEqual(ds.stored("aurora")["engine"], "postgresql")

    def test_missing_driver_is_reported(self):
        ds.save_remote("oracle", {"host": "h", "port": "1521", "database": "svc", "user": "u",
                                  "password": "p"})
        with mock.patch("myapp.datasources._list_oracle", side_effect=ImportError("oracledb")):
            with self.assertRaises(ds.DataSourceError) as cm:
                ds.list_tables("oracle")
        # l'ImportError va gestito dentro list_tables, non propagato
        self.assertEqual(cm.exception.code, "driver-missing")
