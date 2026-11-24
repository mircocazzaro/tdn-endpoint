"""POST /sparql-protected/ con il protocollo hdn-components v1, via HTTP."""

import json
from unittest import mock

from django.test import TestCase, override_settings

from myapp import catalog
from myapp import hdn_components as hc
from myapp.tests.helpers import LevelStores, central_request, logged_in_client
from myapp.tests.test_hdn_service import MOTHER, make_catalog
from myapp.tests.test_query_binding import fake_ontop, sent_queries

ALS = "NCIT:C34373"
EMPTY = {"head": {"vars": []}, "results": {"bindings": []}}


class HttpTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.levels = LevelStores()

    @classmethod
    def tearDownClass(cls):
        cls.levels.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = logged_in_client()

    def post(self, fields, level=6, **headers):
        with override_settings(LEVEL_DB=self.levels[level]):
            return self.client.post("/sparql-protected/", fields, **headers)

    def test_response_is_json_even_with_the_historic_accept(self):
        fields = central_request("q02_L1", disease=ALS)
        with fake_ontop(EMPTY):
            resp = self.post(fields, HTTP_ACCEPT="application/sparql-results+json")
        self.assertEqual(resp["Content-Type"], "application/json")
        body = json.loads(resp.content)
        self.assertEqual((body["protocol"], body["version"]), ("hdn-components", 1))

    def test_response_decodes_with_the_shared_decoder(self):
        fields = central_request("q02_L1", disease=ALS)
        rows = {"head": {"vars": ["pat"]}, "results": {"bindings": [
            {"pat": {"type": "uri", "value": "urn:p:1"}}, {"pat": {"type": "uri", "value": "urn:p:2"}},
            {"pat": {"type": "uri", "value": "urn:p:1"}}]}}
        with fake_ontop(rows):
            resp = self.post(fields)
        decoded = hc.decode(json.loads(resp.content), fields["query"])
        (relation,) = decoded.relations.values()
        self.assertEqual(sorted(relation.values()), [1, 2])      # urn:p:1 due volte

    def test_context_digests_follow_the_active_catalog(self):
        fields = central_request("q02_L1", disease=ALS)
        with fake_ontop(EMPTY):
            a = json.loads(self.post(fields).content)["context"]
        other = catalog.from_document({**catalog.BASE.to_document(), "version": 7,
                                       "templates": [dict(t, description="changed") if t["key"] == "q00_L0" else t
                                                     for t in catalog.BASE.to_document()["templates"]]})
        with fake_ontop(EMPTY), mock.patch.object(catalog, "active", return_value=other):
            b = json.loads(self.post(fields).content)["context"]
        self.assertNotEqual(a["catalog_sha256"], b["catalog_sha256"])
        self.assertEqual(a["ontology_sha256"], b["ontology_sha256"])

    def test_compose_mother_goes_through_the_same_path(self):
        cat = make_catalog()
        query = ("PREFIX t: <urn:t:>\nSELECT ?x ?l WHERE { ?x t:takes ?d . ?d t:label ?l }")
        rows = {"head": {"vars": []}, "results": {"bindings": []}}
        with fake_ontop(rows) as ontop, mock.patch.object(catalog, "active", return_value=cat):
            ok = self.post({"query": query}, level=3)
            refused = self.post({"query": query}, level=2)       # il piano usa un template L3
        self.assertTrue(sent_queries(ontop))
        n_calls = len(sent_queries(ontop))
        self.assertEqual(json.loads(ok.content)["plan_sha256"], json.loads(refused.content)["plan_sha256"])
        self.assertEqual(n_calls, len(json.loads(ok.content)["plan"]["components"]),
                         "il livello L3 basta solo per ok: il rifiuto non esegue nulla")

    def test_analytics_requests_keep_their_own_response(self):
        fields = central_request("q11_L5", disease=ALS)
        with fake_ontop({"results": {"bindings": []}}):
            resp = self.post({**fields, "analytics_key": "klDiv"})
        self.assertIsNone(resp.json()["kl_divergence"])

    @override_settings(HDN_RESPONSE_FORMAT="legacy")
    def test_legacy_switch_restores_the_historic_response(self):
        fields = central_request("q02_L1", disease=ALS)
        with fake_ontop(EMPTY) as ontop:
            resp = self.post(fields)
        self.assertEqual(sent_queries(ontop), [fields["query"]])
        self.assertEqual(json.loads(resp.content), EMPTY)

    def test_missing_query_is_still_a_client_error(self):
        self.assertEqual(self.post({}).status_code, 400)
        self.assertEqual(self.client.get("/sparql-protected/").status_code, 405)

    def test_same_mother_gives_the_same_plan_on_every_call(self):
        fields = {"query": MOTHER}
        with fake_ontop(EMPTY), mock.patch.object(catalog, "active", return_value=make_catalog()):
            a = json.loads(self.post(fields, level=3).content)
            b = json.loads(self.post(fields, level=0).content)
        self.assertEqual(a["plan"], b["plan"])
        self.assertEqual(a["plan_sha256"], b["plan_sha256"])
