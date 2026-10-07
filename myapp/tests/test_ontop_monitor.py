"""Audit-test del punto 32: polling della pagina Ontop Monitor.

Prima la pagina usava due setInterval (2 s e 3 s) senza gestione degli errori:
con il server giu' continuava a sparare richieste, che potevano accavallarsi,
senza dire nulla; il log veniva riscritto e forzato in fondo a ogni ciclo,
quindi non si poteva scorrere indietro; l'interruttore di avvio/arresto
ignorava la risposta del server, e se l'azione falliva restava nella
posizione sbagliata senza segnalazione.
"""

import shutil
import subprocess
from pathlib import Path

from django.test import Client, SimpleTestCase, TestCase

JS_TEST = Path(__file__).resolve().parent / "js" / "ontop_monitor.test.js"
NODE = shutil.which("node")


class OntopMonitorJsTests(SimpleTestCase):

    def test_monitor_module(self):
        if not NODE:
            self.skipTest("node non disponibile")
        r = subprocess.run([NODE, str(JS_TEST)], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class OntopMonitorPageTests(TestCase):

    def test_page_uses_the_monitor_module(self):
        html = Client().get("/ontop-control/").content.decode()
        self.assertIn("/static/myapp/ontop_monitor.js", html)
        self.assertIn("createPoller", html)
        self.assertNotIn("setInterval", html, "polling ancora a intervallo fisso")
        self.assertIn('id="ontopAlert"', html)
