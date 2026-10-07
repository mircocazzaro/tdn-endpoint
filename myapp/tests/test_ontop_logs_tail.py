"""Audit-test del punto 38: /ontop/logs/ ha un costo limitato.

La versione precedente leggeva a blocchi di 1 KB e a ogni blocco rifaceva
splitlines() su tutto il buffer: costo quadratico. Con un file senza a capo,
o con righe lunghe, leggeva l'intero file a ogni richiesta, e Ontop Monitor la
ripete ogni pochi secondi.

Il test usa un file di 3 MB senza a capo: abbastanza piccolo perche' la
versione precedente termini (in alcuni secondi), abbastanza grande da
separare nettamente i due comportamenti.
"""

import os
import tempfile
import time
from pathlib import Path

from django.test import Client, SimpleTestCase

from myapp import ontop_process, views


class OntopLogsTailTests(SimpleTestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "ontop.log"
        self.console = Path(self._tmp.name) / "ontop.console.log"
        for target, name, value in [(views, "LOG_FILE", str(self.log)),
                                    (ontop_process, "CONSOLE_FILE", self.console)]:
            original = getattr(target, name)
            setattr(target, name, value)
            self.addCleanup(setattr, target, name, original)

    def _get(self):
        t0 = time.perf_counter()
        resp = Client().get("/ontop/logs/")
        return resp, time.perf_counter() - t0

    def test_file_without_newlines_is_cheap(self):
        self.log.write_bytes(b"x" * (3 * 1024 * 1024))
        resp, elapsed = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 0.5, f"{elapsed:.2f}s per un file di 3 MB senza a capo")
        lines = resp.json()["lines"]
        self.assertEqual(len(lines), 1)
        self.assertLess(len(lines[0]), 2100, "riga restituita senza troncamento")

    def test_last_lines_are_returned_in_order(self):
        self.log.write_text("".join(f"riga {i}\n" for i in range(10000)))
        lines = self._get()[0].json()["lines"]
        self.assertEqual(lines, [f"riga {i}" for i in range(9800, 10000)])

    def test_short_file_and_missing_file(self):
        self.log.write_text("a\nb\n")
        self.assertEqual(self._get()[0].json()["lines"], ["a", "b"])
        self.log.unlink()
        self.assertEqual(self._get()[0].json()["lines"], [])

    def test_console_is_shown_when_the_start_failed_before_logback(self):
        self.log.write_text("vecchio avvio\n")
        old = time.time() - 60
        os.utime(self.log, (old, old))
        self.console.write_text("===== avvio =====\nError: could not find java\n")
        lines = self._get()[0].json()["lines"]
        self.assertEqual(lines[0], "vecchio avvio")
        self.assertIn("Error: could not find java", lines)

    def test_console_is_hidden_when_logback_took_over(self):
        self.console.write_text("banner\n")
        old = time.time() - 60
        os.utime(self.console, (old, old))
        self.log.write_text("Started OntopEndpointApplication\n")
        self.assertEqual(self._get()[0].json()["lines"], ["Started OntopEndpointApplication"])
