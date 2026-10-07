"""Audit-test del punto 17: nessun valore della richiesta nel testo SQL.

get_columns interpolava il parametro ``table`` della querystring in
``PRAGMA table_info('{table}')``. DuckDB esegue piu' istruzioni in un'unica
execute(): con una GET non autenticata si eseguiva SQL arbitrario, e anche
sulla connessione in sola lettura ``COPY (SELECT ...) TO '<file>'`` scriveva su
disco il contenuto di qualunque tabella. Verificato.

delete_table_view metteva il nome fra doppi apici senza raddoppiare quelli
contenuti nel nome: una tabella con un '"' nel nome non si poteva eliminare.

Il caricamento dei CSV (punto 16) passa il percorso come parametro e quota il
nome della tabella; datastore.failing_sources esegue invece per costruzione
il SQL dei mapping generati, su una connessione in sola lettura.
"""

import os
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.test import Client, TestCase

from myapp import ontop_process, views


class SqlInjectionTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "site.duckdb"
        con = duckdb.connect(str(self.db))
        con.execute('CREATE TABLE "T" (secret VARCHAR)')
        con.execute("INSERT INTO \"T\" VALUES ('dato-riservato')")
        con.execute('CREATE TABLE "it\'s ""quoted""" (a INT)')
        con.close()
        original = views.DUCKDB_PATH
        views.DUCKDB_PATH = str(self.db)
        self.addCleanup(setattr, views, "DUCKDB_PATH", original)
        p = mock.patch.object(ontop_process, "is_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)

    def test_get_columns_does_not_execute_the_parameter(self):
        out = self.dir / "esfiltrato.csv"
        payload = (f"T'); COPY (SELECT * FROM \"T\") TO '{out}'; "
                   "SELECT * FROM pragma_table_info('T")
        resp = Client().get("/get-columns/", {"table": payload})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"columns": []})
        self.assertFalse(out.exists(), "il parametro e' stato eseguito come SQL")

    def test_get_columns_with_awkward_but_real_names(self):
        resp = Client().get("/get-columns/", {"table": 'it\'s "quoted"'})
        self.assertEqual(resp.json(), {"columns": ["a"]})
        self.assertEqual(Client().get("/get-columns/", {"table": "T"}).json(),
                         {"columns": ["secret"]})

    def test_delete_table_with_quotes_in_the_name(self):
        Client().post("/delete-table/" + 'it\'s "quoted"' + "/")
        con = duckdb.connect(str(self.db), read_only=True)
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        con.close()
        self.assertEqual(tables, {"T"})
