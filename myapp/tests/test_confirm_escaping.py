"""Audit-test del punto 30: il nome di tabella non entra nel codice del confirm().

La home scriveva ``onsubmit="return confirm('Really delete table {{ table }}?')"``.
L'escaping automatico di Django e' per HTML, non per JavaScript: il browser
decodifica le entita' dell'attributo prima di eseguirlo, quindi un apice nel
nome della tabella (che viene dal nome del CSV caricato) chiudeva la stringa
JS e il resto del nome veniva eseguito come codice.
"""

import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import duckdb
from django.test import Client, TestCase

from myapp import views

HOSTILE = "x'); alert(document.cookie); ('"


class _Forms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append(dict(attrs))


class ConfirmEscapingTests(TestCase):

    def test_table_name_is_data_not_code(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "s.duckdb")
            con = duckdb.connect(db)
            con.execute(f'CREATE TABLE "{HOSTILE.replace(chr(34), "")}" (a INT)')
            con.close()
            original = views.DUCKDB_PATH
            views.DUCKDB_PATH = db
            try:
                page = Client().get("/").content.decode()
            finally:
                views.DUCKDB_PATH = original

        parser = _Forms()
        parser.feed(page)
        deletes = [f for f in parser.forms if "onsubmit" in f]
        self.assertEqual(len(deletes), 1)
        form = deletes[0]
        # Valori come li vede il browser, dopo la decodifica delle entita'.
        self.assertNotIn("alert", form["onsubmit"],
                         "il nome della tabella e' finito nel codice JavaScript")
        self.assertEqual(form["onsubmit"],
                         "return confirm('Really delete table ' + this.dataset.table"
                         " + '? This cannot be undone.');")
        self.assertEqual(form.get("data-table"), HOSTILE)
