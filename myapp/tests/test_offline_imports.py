"""Audit-test del punto 36: Ontop si avvia senza accesso a Internet.

hero_clinical.ttl dichiara owl:imports di Pollution.owl (University of Toronto)
e di SKOS. Ontop li scarica all'avvio: verificato con Ontop 5.3.0 e la rete
resa irraggiungibile alla JVM (proxy su una porta chiusa), l'endpoint termina
dopo 3 s con UnloadableImportException. In una rete ospedaliera con uscita
filtrata l'endpoint non parte.

Ora le due ontologie sono in myapp/obda/imports/ e un catalogo XML le
reindirizza; ontop_process lo passa a Ontop con -x. Con la stessa rete
irraggiungibile Ontop parte e carica gli stessi assiomi (106 di Pollution,
36 di SKOS nel log) della versione online.

Il test di avvio reale gira se HDN_ONTOP_LIVE=1, perche' avvia una JVM.
"""

import hashlib
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from django.test import SimpleTestCase

from myapp import ontop_process
from myapp.tests.ontop_live import LiveOntop

IMPORTS = Path(ontop_process.ONTOP_DIR) / "imports"
NS = "{urn:oasis:names:tc:entity:xmlns:xml:catalog}"


def _owl_imports(text):
    iris = []
    for block in re.findall(r"owl:imports\s+((?:<[^>]+>\s*,?\s*)+)", text):
        iris += re.findall(r"<([^>]+)>", block)
    iris += re.findall(r'<owl:imports\s+rdf:resource="([^"]+)"', text)
    return iris


def _catalog():
    root = ET.parse(ontop_process.XML_CATALOG).getroot()
    return {e.get("name"): e.get("uri") for e in root.iter(NS + "uri")}


class OfflineImportsTests(SimpleTestCase):

    def test_ontop_is_started_with_the_catalog(self):
        cmd = ontop_process.command()
        self.assertIn("-x", cmd)
        self.assertEqual(Path(cmd[cmd.index("-x") + 1]), Path(ontop_process.XML_CATALOG))

    def test_every_import_is_redirected_to_a_local_file(self):
        ttl = Path(ontop_process.TTL_FILE).read_text(encoding="utf-8")
        imports = _owl_imports(ttl)
        self.assertTrue(imports, "nessun owl:imports trovato: parsing fallito?")
        catalog = _catalog()
        for iri in imports:
            with self.subTest(iri=iri):
                self.assertIn(iri, catalog, "import non coperto dal catalogo")
                self.assertTrue((IMPORTS / catalog[iri]).is_file())

    def test_imported_ontologies_import_nothing_else(self):
        """La chiusura e' completa: nessun import di secondo livello va in rete."""
        for target in _catalog().values():
            with self.subTest(file=target):
                text = (IMPORTS / target).read_text(encoding="utf-8")
                self.assertEqual(_owl_imports(text), [])

    def test_local_copies_match_their_hashes(self):
        for line in (IMPORTS / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((IMPORTS / name).read_bytes()).hexdigest(), digest)


class OntopStartsOfflineTests(SimpleTestCase):
    """Avvio reale di Ontop con la rete irraggiungibile per la JVM."""

    OFFLINE_ENV = dict(os.environ, ONTOP_JAVA_ARGS=(
        "-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 "
        "-Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=9"))

    def setUp(self):
        if os.environ.get("HDN_ONTOP_LIVE") != "1" or not shutil.which("java"):
            self.skipTest("avvio reale di Ontop non richiesto (HDN_ONTOP_LIVE=1)")

    def test_starts_offline_with_the_catalog(self):
        with LiveOntop(["-x", str(ontop_process.XML_CATALOG)], env=self.OFFLINE_ENV) as o:
            self.assertTrue(o.ready, "Ontop non si e' avviato senza rete nonostante il catalogo")

    def test_does_not_start_offline_without_the_catalog(self):
        """Controllo negativo: la simulazione di rete assente e' efficace."""
        with LiveOntop([], env=self.OFFLINE_ENV, deadline=60) as o:
            self.assertFalse(o.ready)
            self.assertIn("UnloadableImport", o.console.read_text())
