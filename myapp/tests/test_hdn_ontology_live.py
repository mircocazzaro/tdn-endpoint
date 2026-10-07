"""Ontop reale con un'ontologia ricevuta da Central (fuori da myapp/obda).

Richiede Java; eseguito solo con HDN_ONTOP_LIVE=1, come gli altri test live.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from myapp import ontop_process

from .ontop_live import LiveOntop


@unittest.skipUnless(os.environ.get("HDN_ONTOP_LIVE") == "1", "HDN_ONTOP_LIVE=1 per i test con Ontop reale")
class ReceivedOntologyStartsOntopTests(SimpleTestCase):
    def test_reserialized_ontology_in_state_dir_loads_with_offline_imports(self):
        import rdflib
        from rdflib.namespace import RDF
        g = rdflib.Graph().parse(ontop_process.TTL_FILE, format="turtle")
        g.remove((rdflib.URIRef("https://w3id.org/brainteaser/ontology/schema/ageOnset"), RDF.type, None))
        with tempfile.TemporaryDirectory() as state:
            ttl = Path(state) / "ontology" / "active.ttl"
            ttl.parent.mkdir()
            ttl.write_bytes(g.serialize(format="turtle").encode())
            with mock.patch.object(ontop_process, "TTL_FILE", ttl):
                with LiveOntop(["-x", str(ontop_process.XML_CATALOG)]) as o:
                    log = o.console.read_text(errors="replace") if not o.ready else ""
                    self.assertTrue(o.ready, log[-2000:])
