"""Strumenti condivisi dai test: richieste nella forma di HDN Central, policy locali."""

import tempfile
from pathlib import Path

import duckdb

from myapp import catalog

LEVEL_LABELS = {
    0: "L0 - Boolean Queries",
    1: "L1 - Simple COUNT Aggregations",
    2: "L2 - Full Aggregations (AVG, ecc.)",
    3: "L3 - Grouped Data",
    4: "L4 - Limited Access to Non\u2010Sensitive Data",
    5: "L5 - Access to Individual Patient Data",
    6: "L6 - Full Access to Data",
}


def central_request(key, **params):
    """Campi POST che HDN Central invia per il template ``key``.

    Riproduce central-tdn/catalogapp/views.py, query_view: sostituzione dei
    segnaposto con str.replace nell'ordine dei parametri, prologo PREFIX in
    testa, e campo ``template`` mascherato.
    """
    template = catalog.BY_KEY[key]
    query = template.sparql
    for name, value in params.items():
        query = query.replace(f"{{{name}}}", value)
    return {
        "template": catalog.central_mask(template.sparql),
        "query": catalog.PROLOGUE + query,
    }


class LevelStores:
    """Un file level.duckdb per ogni livello, creati una volta per classe di test."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = {}
        for level, label in LEVEL_LABELS.items():
            path = str(Path(self._tmp.name) / f"level_{level}.duckdb")
            con = duckdb.connect(path)
            con.execute("CREATE TABLE options (key TEXT PRIMARY KEY, value TEXT)")
            con.execute("INSERT INTO options VALUES ('level', ?)", [label])
            con.close()
            self.paths[level] = path

    def __getitem__(self, level):
        return self.paths[level]

    def cleanup(self):
        self._tmp.cleanup()
