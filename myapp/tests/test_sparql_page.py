"""Audit-test del punto 25: la pagina SPARQL locale gestisce le ASK.

Una ASK restituisce {"head": {}, "boolean": ...}. La view leggeva
data['head']['vars'] senza controllo, quindi ogni ASK finiva nell'except e
la pagina mostrava "Error: 'vars'". I template L0 del catalogo sono ASK:
l'amministratore non poteva provarli in locale. Il test verifica anche che la
chiamata a Ontop abbia un timeout (punto 20).
"""

from unittest import mock

from django.test import Client, TestCase


def _ontop(payload):
    resp = mock.Mock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return mock.patch("requests.post", return_value=resp)


class SparqlPageTests(TestCase):

    def test_ask_is_rendered(self):
        with _ontop({"head": {}, "boolean": True}):
            resp = Client().post("/sparql/", {"sparql_query": "ASK { ?s ?p ?o }"})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["sparql_error"])
        self.assertEqual(resp.context["sparql_results"],
                         {"vars": ["boolean"], "rows": [["true"]]})

    def test_select_is_rendered(self):
        payload = {"head": {"vars": ["n"]},
                   "results": {"bindings": [{"n": {"type": "literal", "value": "3"}}]}}
        with _ontop(payload):
            resp = Client().post("/sparql/", {"sparql_query": "SELECT ?n WHERE {}"})
        self.assertEqual(resp.context["sparql_results"], {"vars": ["n"], "rows": [["3"]]})

    def test_ontop_call_has_a_timeout(self):
        with _ontop({"head": {}, "boolean": False}) as post:
            Client().post("/sparql/", {"sparql_query": "ASK {}"})
        self.assertIsNotNone(post.call_args.kwargs.get("timeout"),
                             "senza timeout un Ontop bloccato blocca il worker")
