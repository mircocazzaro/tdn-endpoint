"""Ontop reale sulle viste di Galois.

Verifica la scelta di fondo della modalita' Galois: Ontop tiene galois.duckdb
aperto in sola lettura, e una riscrittura del Parquet sottostante e' visibile
alla query SPARQL successiva senza riavviare Ontop.

Richiede Java; eseguito solo con HDN_ONTOP_LIVE=1.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.parse
import urllib.request

from django.test import SimpleTestCase, override_settings

from myapp import ontop_process
from myapp.galois import schema, store

from .ontop_live import free_port

QUERY = """PREFIX bto: <https://w3id.org/brainteaser/ontology/schema/>
SELECT ?t ?d ?desc WHERE { ?t a bto:ClinicalTrial ; bto:isAboutDisease ?d .
OPTIONAL { ?t bto:clinicalTrialDescription ?desc } } ORDER BY ?t"""


def _row(nct, desc):
    return {"nct_id": nct, "title": None, "description": desc, "disease_ncit_code": "C34373"}


@unittest.skipUnless(os.environ.get("HDN_ONTOP_LIVE") == "1", "HDN_ONTOP_LIVE=1 per i test con Ontop reale")
class GaloisViewsWithLiveOntopTests(SimpleTestCase):
    def test_parquet_rewrite_visible_without_restart(self):
        with tempfile.TemporaryDirectory() as state, override_settings(HDN_STATE_DIR=state):
            cfg = store.load_config()
            cfg["enabled"] = True
            store.save_config(cfg)
            self.assertEqual(store.apply(cfg), [])
            cols = schema.active_columns(schema.BY_NAME["clinical_trial"], cfg["tables"]["clinical_trial"])
            store._write_parquet(store.parquet_path("clinical_trial"), cols,
                                 [_row("NCT00000001", "first")])

            port = free_port()
            cmd = ontop_process.command() + ["--port", str(port)]
            self.assertIn(str(store.mapping_path()), cmd)
            log = open(os.path.join(state, "ontop.log"), "w")
            proc = subprocess.Popen(cmd, cwd=ontop_process.ONTOP_DIR, stdout=log,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            try:
                def ask():
                    data = urllib.parse.urlencode({"query": QUERY}).encode()
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/sparql", data=data,
                                                 headers={"Accept": "application/sparql-results+json"})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        b = json.load(r)["results"]["bindings"]
                    return [(x["t"]["value"], x["d"]["value"], x.get("desc", {}).get("value")) for x in b]

                deadline, first = time.monotonic() + 150, None
                while time.monotonic() < deadline and proc.poll() is None:
                    try:
                        first = ask()
                        break
                    except Exception:
                        time.sleep(1)
                log.flush()
                self.assertIsNotNone(first, open(os.path.join(state, "ontop.log")).read()[-3000:])
                self.assertEqual(first, [("https://clinicaltrials.gov/study/NCT00000001",
                                          "http://purl.obolibrary.org/obo/NCIT_C34373", "first")])

                store._write_parquet(store.parquet_path("clinical_trial"), cols,
                                     [_row("NCT00000002", "second"), _row("NCT00000003", None)])
                self.assertEqual(ask(), [
                    ("https://clinicaltrials.gov/study/NCT00000002",
                     "http://purl.obolibrary.org/obo/NCIT_C34373", "second"),
                    ("https://clinicaltrials.gov/study/NCT00000003",
                     "http://purl.obolibrary.org/obo/NCIT_C34373", None)])
            finally:
                proc.terminate()
                try:
                    proc.wait(30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                log.close()
