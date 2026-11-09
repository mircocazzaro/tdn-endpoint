"""Ontology Monitor: indice, grafo Mermaid e viste dell'ontologia attiva."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase

from rdflib.namespace import OWL

from myapp import ontology, ontology_monitor

from .hdn_helpers import StateDirMixin

BASE = "https://example.org/onto#"
TTL = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix ex: <{BASE}> .
<urn:o> a owl:Ontology .
ex:Patient a owl:Class ; rdfs:label "Patient"@en ; rdfs:comment "A person under care"@en .
ex:Disease a owl:Class ; rdfs:label "Disease"@en .
ex:Rare a owl:Class ; rdfs:subClassOf ex:Disease ; rdfs:label "Rare disease"@en .
ex:hasDisease a owl:ObjectProperty ; rdfs:domain ex:Patient ; rdfs:range ex:Disease ;
    rdfs:label "has disease"@en .
ex:age a owl:DatatypeProperty ; rdfs:domain ex:Patient .
ex:alice a ex:Patient , owl:NamedIndividual ; rdfs:label "Alice \\"A\\""@en .
ex:flu a ex:Rare .
"""


class IndexTests(SimpleTestCase):
    def setUp(self):
        self.data = ontology_monitor.build(TTL)
        self.by = {t["iri"]: t for t in self.data["terms"]}

    def test_terms_have_kind_label_and_relations(self):
        patient = self.by[BASE + "Patient"]
        self.assertEqual(patient["kind"], "class")
        self.assertEqual(patient["label"], "Patient")
        self.assertEqual(patient["comment"], "A person under care")
        self.assertEqual(self.by[BASE + "Rare"]["parents"], [BASE + "Disease"])
        self.assertEqual(self.by[BASE + "Disease"]["children"], [BASE + "Rare"])

    def test_properties_have_domain_and_range(self):
        prop = self.by[BASE + "hasDisease"]
        self.assertEqual(prop["kind"], "object_property")
        self.assertEqual(prop["domain"], [BASE + "Patient"])
        self.assertEqual(prop["range"], [BASE + "Disease"])
        self.assertEqual(self.by[BASE + "age"]["kind"], "datatype_property")

    def test_individuals_link_to_their_classes(self):
        alice = self.by[BASE + "alice"]
        self.assertEqual(alice["kind"], "individual")
        self.assertEqual(alice["types"], [BASE + "Patient"])
        self.assertEqual(alice["label"], 'Alice "A"')
        self.assertIn(BASE + "alice", self.by[BASE + "Patient"]["instances"])
        self.assertNotIn(str(OWL.NamedIndividual), alice["types"])

    def test_counts_and_ontology_iri(self):
        self.assertEqual(self.data["counts"]["class"], 3)
        self.assertEqual(self.data["counts"]["individual"], 2)  # alice e flu
        self.assertEqual(self.data["ontology"], ["urn:o"])

    def test_graph_of_a_class_shows_neighbours(self):
        text, truncated = ontology_monitor.graph(self.data, BASE + "Disease")
        self.assertFalse(truncated)
        self.assertTrue(text.startswith("flowchart LR"))
        self.assertIn('["Disease"]', text)
        self.assertIn('["Rare disease"]', text)
        self.assertIn("|subClassOf|", text)
        self.assertIn("class n0 focus", text)

    def test_graph_of_a_property_is_dashed_to_domain_and_range(self):
        text, _ = ontology_monitor.graph(self.data, BASE + "hasDisease")
        self.assertIn("-.->|domain|", text)
        self.assertIn("-.->|range|", text)

    def test_labels_are_escaped_for_mermaid(self):
        text, _ = ontology_monitor.graph(self.data, BASE + "alice")
        self.assertIn("[\"Alice 'A'\"]", text)
        self.assertNotIn('"A"', text)

    def test_graph_is_truncated_at_the_node_limit(self):
        # Patient ha un'istanza (alice): con un solo nodo il grafo viene troncato
        text, truncated = ontology_monitor.graph(self.data, BASE + "Patient", max_nodes=1)
        self.assertTrue(truncated)
        self.assertEqual(text.count('["'), 1)
        self.assertNotIn("alice", text)

    def test_unknown_term_raises_key_error(self):
        with self.assertRaises(KeyError):
            ontology_monitor.graph(self.data, BASE + "Nope")


class IndexCacheTests(SimpleTestCase):
    def test_index_is_rebuilt_when_the_file_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "onto.ttl"
            path.write_text(TTL, encoding="utf-8")
            first = ontology_monitor.index(path)
            self.assertIs(first, ontology_monitor.index(path))
            path.write_text(TTL + f"<{BASE}New> a <http://www.w3.org/2002/07/owl#Class> .\n",
                            encoding="utf-8")
            second = ontology_monitor.index(path)
            self.assertIsNot(first, second)
            self.assertIn(BASE + "New", {t["iri"] for t in second["terms"]})


class OntologyMonitorViewTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ttl = Path(tmp.name) / "active.ttl"
        self.ttl.write_text(TTL, encoding="utf-8")
        p = mock.patch.object(ontology, "active_path", lambda: self.ttl)
        p.start()
        self.addCleanup(p.stop)

    def test_page_embeds_the_index_and_explorer(self):
        resp = self.client.get("/ontology/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ontology Monitor")
        self.assertContains(resp, 'id="ontology-index"')
        self.assertContains(resp, "ontology_monitor.js")
        self.assertContains(resp, "Upload a new ontology")
        page = resp.content.decode()
        start = page.index('id="ontology-index"')
        payload = page[page.index(">", start) + 1:page.index("</script>", start)]
        self.assertIn(BASE + "Patient", {t["iri"] for t in json.loads(payload)["terms"]})

    def test_page_shows_an_error_when_the_ontology_is_unreadable(self):
        self.ttl.write_text("not turtle {", encoding="utf-8")
        resp = self.client.get("/ontology/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cannot be read")
        self.assertContains(resp, "Upload a new ontology")

    def test_graph_endpoint_returns_mermaid(self):
        resp = self.client.get("/ontology/graph/", {"iri": BASE + "Patient"})
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertTrue(body["mermaid"].startswith("flowchart LR"))
        self.assertFalse(body["truncated"])

    def test_graph_endpoint_rejects_unknown_terms(self):
        resp = self.client.get("/ontology/graph/", {"iri": BASE + "Nope"})
        self.assertEqual(resp.status_code, 404)

    def test_graph_endpoint_only_accepts_get(self):
        self.assertEqual(self.client.post("/ontology/graph/", {}).status_code, 405)
