"""Audit-test del punto 16: caricamento dei CSV.

Prima:

- CREATE TABLE IF NOT EXISTS: ricaricare un CSV corretto non cambiava nulla,
  e l'amministratore vedeva comunque "uploaded and ingested";
- il nome della tabella era derivato dal nome con cui il file veniva salvato
  su disco: FileSystemStorage aggiunge un suffisso casuale se il nome e' gia'
  usato (es. Turin_alsfrs_afDiDky), e spazi e trattini diventavano '_', per
  cui "PATIENTS GENERAL DATA.csv" non produceva mai la tabella
  "PATIENTS GENERAL DATA" che il template di mapping usa;
- se l'ingestione falliva, il CSV restava in uploads/, che e' servito via
  /media/ con DEBUG attivo;
- con piu' file, un errore a meta' lasciava caricati i precedenti.

Ontop e' simulato come spento: il suo arresto e riavvio e' coperto dal punto 11.
"""

import io
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings

from myapp import ontop_process, views


# UTF-8 non valido: read_csv_auto lo rifiuta. Una quota non chiusa o righe con
# un numero variabile di colonne, invece, vengono accettate dal sniffer.
BROKEN = b"a\n\xff\xfe\x00\n"


def _csv(name, text):
    f = io.BytesIO(text if isinstance(text, bytes) else text.encode("utf-8"))
    f.name = name
    return f


class CsvUploadTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.media = tmp / "uploads"
        self.media.mkdir()
        self.db = tmp / "site.duckdb"
        media = override_settings(MEDIA_ROOT=str(self.media))
        media.enable()
        self.addCleanup(media.disable)
        original = views.DUCKDB_PATH
        views.DUCKDB_PATH = str(self.db)
        self.addCleanup(setattr, views, "DUCKDB_PATH", original)
        p = mock.patch.object(ontop_process, "is_running", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        self.client = Client()

    def _upload(self, *files):
        # Client nuovo per ogni upload: i messaggi di una risposta in redirect
        # restano in coda finche' non vengono mostrati, e si sommerebbero.
        resp = Client().post("/upload-csv/", {"csv_files": list(files)})
        return resp, [(m.level_tag, str(m)) for m in get_messages(resp.wsgi_request)]

    def _rows(self, table):
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            return con.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall()
        finally:
            con.close()

    def _tables(self):
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            return sorted(r[0] for r in con.execute("SHOW TABLES").fetchall())
        finally:
            con.close()

    def test_table_name_is_the_file_name(self):
        """Il nome del file, spazi compresi, e' il nome della tabella del template."""
        self._upload(_csv("PATIENTS GENERAL DATA.csv", "patient,sex\np1,f\n"))
        self.assertEqual(self._tables(), ["PATIENTS GENERAL DATA"])

    def test_reupload_replaces_the_table_and_says_so(self):
        self._upload(_csv("visits.csv", "patient,age\np1,60\n"))
        _, msgs = self._upload(_csv("visits.csv", "patient,age\np1,61\np2,70\n"))
        self.assertEqual(self._rows("visits"), [("p1", 61), ("p2", 70)])
        self.assertEqual(self._tables(), ["visits"], "creata una tabella con suffisso")
        self.assertTrue(any(lvl == "success" and "replaced" in t and "visits" in t
                            for lvl, t in msgs), msgs)

    def test_no_file_is_left_in_uploads(self):
        self._upload(_csv("ok.csv", "a\n1\n"))
        self._upload(_csv("broken.csv", BROKEN))
        self.assertEqual(list(self.media.iterdir()), [],
                         "file rimasto in uploads/, servito via /media/")

    def test_failed_file_rolls_back_the_whole_upload(self):
        self._upload(_csv("keep.csv", "a\n1\n"))
        _, msgs = self._upload(_csv("keep.csv", "a\n2\n"),
                               _csv("broken.csv", BROKEN))
        self.assertEqual(self._rows("keep"), [(1,)], "upload parziale applicato")
        self.assertNotIn("broken", self._tables())
        self.assertEqual([lvl for lvl, _ in msgs], ["error"])
        self.assertIn("broken.csv", msgs[0][1])

    def test_invalid_names_are_rejected(self):
        for name in [".csv", "   .csv", "x" * 200 + ".csv"]:
            with self.subTest(name=name):
                _, msgs = self._upload(_csv(name, "a\n1\n"))
                self.assertEqual([lvl for lvl, _ in msgs], ["error"])
        self.assertFalse(self.db.exists() and self._tables())
