"""Audit-test del punto 37: il log di Ontop ha dimensione limitata e meno rumore.

Prima l'output di Ontop finiva in append in myapp/obda/ontop.log senza alcun
limite: un endpoint resta acceso per mesi. A ogni avvio vi si aggiungevano
~190 avvisi identici ("Axiom does not belong to OWL 2 QL", ridichiarazioni
di entita' della chiusura di import), dovuti a HERO e non azionabili dal sito,
che seppellivano gli errori reali nella console di Ontop Monitor.

Ora Ontop e' avviato con myapp/obda/log/logback-endpoint.xml: log su
ontop.log con rotazione a 10 MB e 5 file precedenti, avvisi OWL 2 QL a ERROR.

Il test reale avvia logback (con le librerie di Ontop) e, con HDN_ONTOP_LIVE=1,
Ontop stesso.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from myapp import ontop_process
from myapp.tests.ontop_live import LiveOntop

LIB = Path(ontop_process.ONTOP_DIR) / "lib"
JAVA = shutil.which("java")

# Scrive molte righe attraverso logback con la configurazione dell'endpoint.
FLOOD = r"""
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
public class Flood {
    public static void main(String[] a) {
        Logger app = LoggerFactory.getLogger("it.unibz.inf.ontop.Test");
        Logger ql = LoggerFactory.getLogger("it.unibz.inf.ontop.spec.ontology.owlapi.OWLAPITranslatorOWL2QL");
        String pad = "x".repeat(200);
        for (int i = 0; i < 20000; i++) app.info("riga {} {}", i, pad);
        ql.warn("Axiom does not belong to OWL 2 QL: rumore");
        app.error("ERRORE REALE");
    }
}
"""


class LoggingConfigurationTests(SimpleTestCase):

    def test_ontop_is_started_with_the_endpoint_log_config(self):
        env = ontop_process.environment()
        self.assertEqual(Path(env["ONTOP_LOG_CONFIG"]), Path(ontop_process.LOG_CONFIG))
        self.assertEqual(Path(env["HDN_ONTOP_LOG"]), Path(ontop_process.LOG_FILE))
        self.assertTrue(Path(ontop_process.LOG_CONFIG).is_file())

    def test_rotation_with_the_real_logback(self):
        if not JAVA:
            self.skipTest("java non disponibile")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "Flood.java").write_text(FLOOD)
            log = d / "ontop.log"
            r = subprocess.run(
                [JAVA, "-cp", f"{LIB}/*",
                 f"-Dlogback.configurationFile={ontop_process.LOG_CONFIG}",
                 f"-DHDN_ONTOP_LOG={log}", "-DHDN_ONTOP_LOG_MAX_SIZE=1MB",
                 str(d / "Flood.java")],
                capture_output=True, text=True, timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            files = sorted(p.name for p in d.iterdir() if p.name.startswith("ontop.log"))
            self.assertIn("ontop.log.1", files, f"nessuna rotazione: {files}")
            self.assertNotIn("ontop.log.6", files, "rotazione oltre 5 file")
            total = sum(p.stat().st_size for p in d.iterdir() if p.name.startswith("ontop.log"))
            self.assertLess(total, 7 * 1024 * 1024)
            self.assertLess(log.stat().st_size, 1.2 * 1024 * 1024)

            current = log.read_text()
            self.assertIn("ERRORE REALE", current)
            self.assertNotIn("does not belong to OWL 2 QL", current)


class OntopLiveLoggingTests(SimpleTestCase):

    def test_real_ontop_writes_the_rotating_log_without_owl_noise(self):
        if os.environ.get("HDN_ONTOP_LIVE") != "1" or not JAVA:
            self.skipTest("avvio reale di Ontop non richiesto (HDN_ONTOP_LIVE=1)")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "ontop.log"
            env = dict(ontop_process.environment(), HDN_ONTOP_LOG=str(log))
            with LiveOntop(["-x", str(ontop_process.XML_CATALOG)], env=env) as o:
                self.assertTrue(o.ready)
                console = o.console.read_text()
            text = log.read_text()
        self.assertIn("Started OntopEndpointApplication", text)
        self.assertEqual(text.count("does not belong to OWL 2 QL"), 0)
        self.assertNotIn("Started OntopEndpointApplication", console,
                         "log duplicato sulla console")
