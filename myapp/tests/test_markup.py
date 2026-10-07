"""Audit-test del punto 28: il markup di ogni pagina dell'interfaccia e' bilanciato.

home.html apriva 5 <div> e ne chiudeva 3: la card dell'upload restava aperta e
il layout del resto della pagina dipendeva da come il browser la ricuciva.
"""

import tempfile
from pathlib import Path

import duckdb
from django.test import Client, TestCase

from myapp import views
from myapp.tests.test_mapping_ui import unbalanced

PAGES = ["/", "/query/", "/sparql/", "/ontop-control/", "/map-fields/"]


class MarkupTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        db = str(Path(self._tmp.name) / "site.duckdb")
        con = duckdb.connect(db)
        # Una tabella con nomi scomodi, cosi' che anche lo schema disegnato
        # nella home finisca nell'HTML verificato.
        con.execute('CREATE TABLE "PATIENTS GENERAL DATA" ("patient id" VARCHAR, sex VARCHAR)')
        con.close()
        original = views.DUCKDB_PATH
        views.DUCKDB_PATH = db
        self.addCleanup(setattr, views, "DUCKDB_PATH", original)

    def test_every_page_is_balanced(self):
        client = Client()
        for page in PAGES:
            with self.subTest(page=page):
                resp = client.get(page)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(unbalanced(resp.content.decode()), [])
