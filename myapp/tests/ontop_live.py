"""Avvio di Ontop reale nei test: porta libera, scadenza totale, arresto garantito.

Una porta fissa faceva collegare un test a un Ontop rimasto da un'esecuzione
precedente interrotta, invece che al proprio, con il test bloccato fino al
timeout.
"""

import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import duckdb

from myapp import ontop_process

MAPPING = (
    "[PrefixDeclaration]\nbto:\t\thttps://w3id.org/brainteaser/ontology/schema/\n\n"
    "[MappingDeclaration] @collection [[\nmappingId\tM1\n"
    "target\t\tbto:Patient{p} a bto:Patient . \nsource\t\tSELECT p FROM \"T\"\n]]\n"
)
QUERY = ("PREFIX bto: <https://w3id.org/brainteaser/ontology/schema/> "
         "SELECT (COUNT(?s) AS ?n) WHERE { ?s a bto:Patient }")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveOntop:
    """Ontop su dati sintetici in una directory temporanea.

    Uso: ``with LiveOntop(extra_args, env) as o: o.ready`` (bool).
    """

    def __init__(self, extra_args=(), env=None, deadline=120):
        self.extra_args = list(extra_args)
        self.env = env
        self.deadline = deadline
        self.ready = False

    def __enter__(self):
        self.work = Path(tempfile.mkdtemp())
        db = self.work / "d.duckdb"
        con = duckdb.connect(str(db))
        con.execute('CREATE TABLE "T" (p VARCHAR)')
        con.execute("INSERT INTO \"T\" VALUES ('x')")
        con.close()
        (self.work / "m.obda").write_text(MAPPING)
        (self.work / "p.properties").write_text(
            f"jdbc.url = jdbc:duckdb:{db}\njdbc.driver = org.duckdb.DuckDBDriver\n"
            "jdbc.property.duckdb.read_only = true\n")
        self.port = free_port()
        self.console = self.work / "console.log"
        cmd = [str(ontop_process.ONTOP_CMD), "endpoint", "-m", str(self.work / "m.obda"),
               "-t", str(ontop_process.TTL_FILE), "-p", str(self.work / "p.properties"),
               "--port", str(self.port)] + self.extra_args
        self._out = open(self.console, "w")
        self.proc = subprocess.Popen(cmd, cwd=ontop_process.ONTOP_DIR, stdout=self._out,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=self.env)
        self.ready = self._wait()
        return self

    def _wait(self):
        data = urllib.parse.urlencode({"query": QUERY}).encode()
        end = time.monotonic() + self.deadline
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{self.port}/sparql", data=data,
                    headers={"Accept": "application/sparql-results+json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status == 200
            except Exception:
                time.sleep(1)
        return False

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(30)
        self._out.close()
        shutil.rmtree(self.work, ignore_errors=True)
        return False
