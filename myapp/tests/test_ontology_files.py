"""Audit-test del punto 39: una sola ontologia attiva, nessuna copia inutilizzata.

myapp/obda conteneva due ontologie: hero_clinical.ttl, quella che Ontop carica
(ontop_process.TTL_FILE), e hereditary_ontology_2.ttl, 653 KB mai usati da
nessuna parte del codice. Con due file simili accanto, un partner che
aggiorna o ispeziona "l'ontologia" puo' modificare quello sbagliato senza
alcun effetto, o puntarci Ontop a mano.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from django.test import SimpleTestCase

from myapp import ontop_process

ONTOLOGY_SUFFIXES = {".ttl", ".owl", ".rdf", ".nt", ".jsonld"}
NS = "{urn:oasis:names:tc:entity:xmlns:xml:catalog}"


class OntologyFilesTests(SimpleTestCase):

    def test_every_ontology_file_is_used(self):
        obda = Path(ontop_process.ONTOP_DIR)
        used = {Path(ontop_process.TTL_FILE).resolve()}
        catalog = Path(ontop_process.XML_CATALOG)
        for e in ET.parse(catalog).getroot().iter(NS + "uri"):
            used.add((catalog.parent / e.get("uri")).resolve())

        present = {p.resolve() for p in obda.rglob("*")
                   if p.is_file() and p.suffix.lower() in ONTOLOGY_SUFFIXES
                   and "lib" not in p.relative_to(obda).parts}
        self.assertEqual(sorted(str(p.relative_to(obda.resolve())) for p in present - used), [],
                         "ontologie presenti ma non caricate da Ontop")
        self.assertTrue(Path(ontop_process.TTL_FILE).is_file())
