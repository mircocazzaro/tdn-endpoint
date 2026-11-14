"""Sito attivo: dialetti SQL, catalogo, validazione e proprieta' di Ontop per sorgente.

Motori remoti: connessioni simulate (le prove con container reali sono separate).
SQLite: file reali.
"""
import os
import sqlite3
import stat
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from .helpers import logged_in_client

from myapp import datasources as ds
from myapp import site
from myapp.dialects import DUCKDB, MSSQL, MYSQL, ORACLE, POSTGRESQL, SQLITE
from myapp.obda_mapping import adapt_isnan

from .hdn_helpers import StateDirMixin


class DialectRewriteTests(TestCase):

    def test_duckdb_is_unchanged(self):
        src = 'SELECT patient, w FROM "t" WHERE NOT isnan(w)'
        out, adapted = adapt_isnan(src, {"w": "VARCHAR"}, DUCKDB)
        self.assertIn("regexp_full_match(CAST(w AS VARCHAR)", out)
        self.assertEqual(adapted, ["w"])
        self.assertIn("CAST(w AS DOUBLE)", out)

    def test_numeric_column_becomes_null_test_outside_duckdb(self):
        out, adapted = adapt_isnan('SELECT a FROM "t" WHERE NOT isnan(age) AND isnan(h)',
                                   {"age": "DOUBLE", "h": "DOUBLE"}, POSTGRESQL)
        self.assertIn("(age IS NOT NULL)", out)
        self.assertIn("(h IS NULL)", out)
        self.assertNotIn("isnan", out.lower())
        self.assertEqual(adapted, [])

    def test_text_column_uses_the_dialect_number_test_and_cast(self):
        src = 'SELECT patient, w FROM "t" WHERE NOT isnan(w)'
        for dialect, cast in ((MYSQL, "CAST(w AS DOUBLE)"),
                              (POSTGRESQL, "CAST(w AS DOUBLE PRECISION)"),
                              (ORACLE, "TO_NUMBER(w)"),
                              (MSSQL, "CAST(w AS FLOAT)"),
                              (SQLITE, "CAST(w AS REAL)")):
            out, adapted = adapt_isnan(src, {"w": "VARCHAR"}, dialect)
            self.assertNotIn("isnan", out.lower(), dialect)
            self.assertIn(cast, out, dialect)
            self.assertIn("CASE WHEN", out, dialect)
            self.assertEqual(adapted, ["w"], dialect)

    def test_number_regex_uses_a_character_class_not_escapes(self):
        # Ontop raddoppia i backslash nei literal: la regex non ne contiene.
        for dialect in (MYSQL, POSTGRESQL, ORACLE):
            out, _ = adapt_isnan('SELECT w FROM "t" WHERE NOT isnan(w)', {"w": "VARCHAR"}, dialect)
            self.assertIn("[.]", out, dialect)
            self.assertNotIn("\\", out, dialect)


class TypeNormalizationTests(TestCase):

    def test_engine_types_map_to_duckdb_names(self):
        n = ds.normalize_type
        self.assertEqual(n("varchar(20)"), "VARCHAR")
        self.assertEqual(n("NVARCHAR"), "VARCHAR")
        self.assertEqual(n("VARCHAR2"), "VARCHAR")
        self.assertEqual(n("int"), "BIGINT")
        self.assertEqual(n("NUMBER"), "DOUBLE")
        self.assertEqual(n("decimal(10,2)"), "DOUBLE")
        self.assertEqual(n("datetime"), "TIMESTAMP")
        self.assertEqual(n("date"), "DATE")
        self.assertEqual(n(""), "VARCHAR")


class OntopPropertiesTests(StateDirMixin, TestCase):

    def _save(self, kind, **values):
        base = {"host": "db.example", "port": "", "database": "studi", "user": "ana", "password": "p:w d=1"}
        base.update(values)
        ds.save_remote(kind, base)

    def test_mysql_url_uses_ansi_quotes_and_escapes_the_password(self):
        self._save("mysql")
        text = site.ontop_properties_text("mysql")
        self.assertIn(r"jdbc.url = jdbc\:mysql\://db.example\:3306/studi", text)
        self.assertIn("ANSI_QUOTES", text)
        self.assertIn("jdbc.driver = com.mysql.cj.jdbc.Driver", text)
        self.assertIn(r"jdbc.password = p\:w\ d\=1", text)

    def test_postgresql_oracle_mssql_drivers(self):
        self._save("postgresql")
        self.assertIn("org.postgresql.Driver", site.ontop_properties_text("postgresql"))
        self._save("oracle", port="1521", database="svc")
        self.assertIn("oracle.jdbc.OracleDriver", site.ontop_properties_text("oracle"))
        self.assertIn("jdbc:oracle:thin:@//", site.ontop_properties_text("oracle").replace("\\", ""))
        self._save("mssql", port="1433", database="db")
        text = site.ontop_properties_text("mssql")
        self.assertIn("com.microsoft.sqlserver.jdbc.SQLServerDriver", text)
        self.assertIn(r"trustServerCertificate\=false", text)

    def test_aurora_uses_the_chosen_engine_driver(self):
        self._save("aurora", engine="postgresql", port="")
        self.assertIn("org.postgresql.Driver", site.ontop_properties_text("aurora"))

    def test_sqlite_file_is_opened_read_only(self):
        ds.store_file("sqlite", [b"SQLite format 3\x00" + b"\x00" * 84])
        text = site.ontop_properties_text("sqlite")
        self.assertIn("org.sqlite.JDBC", text)
        self.assertIn("open_mode = 1", text)

    def test_properties_file_is_private(self):
        self._save("mysql")
        with mock.patch.object(site.ds, "active", return_value="mysql"):
            target = site.write_ontop_properties()
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        self.assertIn("jdbc.password", Path(target).read_text())


class SiteTests(StateDirMixin, TestCase):

    def test_local_site_uses_duckdb_dialect_and_repository_properties(self):
        self.assertTrue(site.is_local())
        self.assertEqual(site.dialect(), DUCKDB)

    def test_sqlite_site_dialect_and_mapping_mismatch(self):
        ds.store_file("sqlite", [b"SQLite format 3\x00" + b"\x00" * 84])
        ds.set_active("sqlite")
        self.assertEqual(site.dialect(), SQLITE)
        site.record_mapping_dialect()          # mapping generato per il sito attivo
        self.assertTrue(site.mapping_matches_site())
        ds.set_active("csv")
        self.assertFalse(site.mapping_matches_site())

    def test_sqlite_validation_reports_only_failing_sources(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (id INTEGER, w TEXT)")
        con.execute("INSERT INTO t VALUES (1, 'x')")
        con.commit()
        con.close()
        ds.store_file("sqlite", [open(path, "rb").read()])
        os.unlink(path)
        ds.set_active("sqlite")
        failing = site.failing_sources(None, [("ok", "SELECT id FROM t"),
                                              ("bad", "SELECT nope FROM t")])
        self.assertEqual(list(failing), ["bad"])
        self.assertIn("no such column", failing["bad"])

    def test_sqlite_schema_is_normalized(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (id INTEGER, w TEXT, v REAL)")
        con.commit()
        con.close()
        ds.store_file("sqlite", [open(path, "rb").read()])
        os.unlink(path)
        ds.set_active("sqlite")
        self.assertEqual(ds.column_types("sqlite")["t"],
                         {"id": "BIGINT", "w": "VARCHAR", "v": "DOUBLE"})
        self.assertEqual(site.tables_columns(None), {"t": ["id", "w", "v"]})

    def test_remote_schema_query_is_read_by_engine(self):
        ds.save_remote("mysql", {"host": "h", "port": "3306", "database": "d", "user": "u",
                                 "password": "p"})
        con = mock.MagicMock()
        con.cursor.return_value.fetchall.return_value = [
            ("esami", "id", "int"), ("esami", "nota", "varchar")]
        with mock.patch("pymysql.connect", return_value=con):
            types = ds.column_types("mysql")
        self.assertEqual(types, {"esami": {"id": "BIGINT", "nota": "VARCHAR"}})

    def test_errors_from_the_remote_source_are_user_messages(self):
        from myapp import views
        exc = ds.DataSourceError("unreachable", "OperationalError: no route")
        self.assertEqual(views.site_error(exc), "Data source: OperationalError: no route")


class SiteViewTests(StateDirMixin, TestCase):

    def _sqlite_source(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE "pazienti" (id INTEGER, eta REAL, peso TEXT)')
        con.execute("INSERT INTO pazienti VALUES (1, 61.5, '72.3')")
        con.commit()
        con.close()
        ds.store_file("sqlite", [open(path, "rb").read()])
        os.unlink(path)
        ds.set_active("sqlite")

    def test_get_columns_reads_the_active_source(self):
        self._sqlite_source()
        r = logged_in_client().get("/get-columns/", {"table": "pazienti"})
        self.assertEqual(r.json()["columns"], ["id", "eta", "peso"])

    def test_mapping_page_renders_with_a_remote_style_source(self):
        self._sqlite_source()
        r = logged_in_client().get("/map-fields/")
        self.assertEqual(r.status_code, 200)


class SqliteKeyTests(StateDirMixin, TestCase):

    def _file(self, ddl):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute(ddl)
        con.commit()
        con.close()
        ds.store_file("sqlite", [open(path, "rb").read()])
        os.unlink(path)

    def test_nullable_primary_key_is_reported(self):
        self._file("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT)")
        self.assertEqual(ds.sqlite_nullable_keys(), ["t.id"])

    def test_not_null_primary_key_is_accepted(self):
        self._file("CREATE TABLE t (id INTEGER NOT NULL PRIMARY KEY, x TEXT)")
        self.assertEqual(ds.sqlite_nullable_keys(), [])

    def test_upload_warns_about_nullable_keys(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()
        f = SimpleUploadedFile("db.sqlite", open(path, "rb").read(), content_type="application/octet-stream")
        os.unlink(path)
        r = logged_in_client().post("/data/", {"kind": "sqlite", "file": f}, follow=True)
        self.assertContains(r, "Ontop refuses primary keys that can be NULL")
