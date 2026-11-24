"""Audit-test del punto 5: i rifiuti devono essere indistinguibili dall'assenza di dati.

D3.2 sez. 2.1.1, 2.2.3 e 2.4.4 richiedono che errori di trasporto, fallimenti
di autorizzazione e rifiuti espliciti per privacy siano tutti normalizzati allo
stesso comportamento osservabile: l'endpoint non contribuisce. Prima della
correzione l'endpoint rispondeva:

    - rifiuto / template ignoto  -> 200 {"results": []}        (``results`` lista)
    - risposta reale             -> 200 {"head":..,"results":{"bindings":[..]}}
    - errore Ontop               -> 502 {"error": "<eccezione Java>"}
    - catalogo non disponibile   -> 500 {"error": "<eccezione>"}

cioe' quattro casi distinguibili fra loro, due dei quali con stato HTTP diverso
e con stato interno nel corpo.

I test usano i template reali del catalogo, nella forma esatta in cui HDN
Central li istanzia, e verificano la proprieta' che conta: tutti gli esiti
negativi producono *la stessa* risposta, byte per byte.
"""

import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from .helpers import logged_in_client

from myapp import catalog, views
from myapp.tests.helpers import LevelStores, central_request

ALS = "NCIT:C34373"

# Query fuori catalogo con la stessa proiezione di q02_L1: serve a confrontare
# un "template ignoto" con gli altri esiti negativi a parita' di forma.
UNKNOWN_COUNT_QUERY = catalog.PROLOGUE + (
    "SELECT (COUNT(DISTINCT ?pat) AS ?nDISEASE) WHERE {\n"
    "  ?pat ?p ?o .\n"
    "}"
)


class DisclosureIndistinguishabilityTests(TestCase):
    """Ogni mancato contributo deve essere osservazionalmente identico."""

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
        # ONTOP_SPARQL_ENDPOINT punta a una porta chiusa: le richieste che
        # superano i controlli falliscono sul backend, che e' uno dei casi
        # negativi da rendere indistinguibile dagli altri.
        overrides = override_settings(
            LEVEL_DB=self.levels[2],
            ONTOP_SPARQL_ENDPOINT="http://127.0.0.1:1/sparql",
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

    def _post(self, fields, level=None):
        if level is None:
            return self.client.post("/sparql-protected/", fields)
        with override_settings(LEVEL_DB=self.levels[level]):
            return self.client.post("/sparql-protected/", fields)

    def _audit(self, fields, level=None):
        captured = []
        original = views.audit.info
        views.audit.info = lambda msg, *a, **k: captured.append(msg % a if a else msg)
        try:
            self._post(fields, level)
        finally:
            views.audit.info = original
        return " ".join(captured)

    # ------------------------------------------------------------------ forma

    def test_refusal_has_the_v1_envelope_with_the_public_plan(self):
        """Un rifiuto ha la struttura di una risposta v1: piano pubblico e nessun dato.

        Il piano dipende solo dalla query e dai metadati condivisi, quindi la
        sua presenza non rivela nulla del rifiuto.
        """
        resp = self._post({
            "template": "ASK WHERE { ?s ?p ?o }",
            "query": catalog.PROLOGUE + "ASK WHERE { ?s ?p ?o }",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/json")
        body = json.loads(resp.content)
        self.assertEqual((body["protocol"], body["version"]), ("hdn-components", 1))
        self.assertEqual((body["tuple_pool"], body["memberships"]), ([], []))
        self.assertTrue(body["plan"]["evaluation_query"].strip().endswith("ASK WHERE { ?s ?p ?o }"))

    def test_unplannable_refusal_uses_a_null_plan(self):
        resp = self._post({"query": catalog.PROLOGUE + "SELECT ?s WHERE { ?s bto:a/bto:b ?o }"})
        body = json.loads(resp.content)
        self.assertIsNone(body["plan"])
        self.assertIsNone(body["plan_sha256"])
        self.assertEqual((body["tuple_pool"], body["memberships"]), ([], []))

    # -------------------------------------------------------- indistinguibilita'

    def _public(self, resp):
        """Corpo della risposta senza ``context``: i digest del catalogo, dell'ontologia
        e del profilo sono metadati condivisi, uguali per chiunque abbia lo stesso stato."""
        body = json.loads(resp.content)
        body.pop("context")
        return body

    def test_all_negative_outcomes_are_byte_identical(self):
        """La proprieta' centrale del punto 5, sulla stessa madre.

        Tre cause diverse - madre fuori dal catalogo, rifiuto per livello,
        backend irraggiungibile - producono la stessa risposta pubblica e lo
        stesso stato HTTP.
        """
        q02 = central_request("q02_L1", disease=ALS)
        without_q02 = catalog.from_document({
            **catalog.BASE.to_document(), "version": 1,
            "templates": [t for t in catalog.BASE.to_document()["templates"] if t["key"] != "q02_L1"],
        })

        with mock.patch.object(catalog, "active", return_value=without_q02):
            unknown = self._post(q02, level=2)          # (a) madre fuori catalogo
        responses = {
            "unknown-template": unknown,
            "disclosure-refused": self._post(q02, level=0),   # (b) L1 su endpoint L0
            "backend-error": self._post(q02, level=2),        # (c) Ontop non risponde
        }

        for name, resp in responses.items():
            with self.subTest(outcome=name):
                self.assertEqual(resp.status_code, 200,
                                 f"{name}: lo stato HTTP rivela la causa")
                self.assertNotIn(b"error", resp.content.lower(),
                                 f"{name}: il corpo rivela stato interno")

        bodies = {name: json.dumps(self._public(resp), sort_keys=True) for name, resp in responses.items()}
        self.assertEqual(len(set(bodies.values())), 1,
                         "gli esiti negativi sono distinguibili: " + repr(bodies))
        # Rifiuto e guasto, con lo stesso catalogo, coincidono anche nel contesto.
        self.assertEqual(responses["disclosure-refused"].content, responses["backend-error"].content)

    def test_refusal_is_identical_to_a_genuinely_empty_answer(self):
        """Un rifiuto deve coincidere con cio' che il sito risponderebbe se non
        ci fossero pazienti che matchano."""
        q02 = central_request("q02_L1", disease=ALS)
        refused = self._post(q02, level=0)
        with mock.patch("requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {"head": {"vars": ["pat"]}, "results": {"bindings": []}}
            genuine_empty = self._post(q02, level=2)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(refused.content, genuine_empty.content)

    def test_level_is_enforced_in_the_right_direction(self):
        """Un template al di sotto del massimo locale non viene rifiutato dal
        controllo di livello.

        Serve come controllo negativo: se questo test passasse anche con il
        livello invertito, i test precedenti non proverebbero nulla.
        """
        audit = self._audit(central_request("q02_L1", disease=ALS), level=2)
        self.assertIn("backend-error", audit,
                      "L1 su endpoint L2 doveva superare il controllo di livello "
                      f"e fallire sul backend; audit: {audit}")
        self.assertNotIn("disclosure-refused", audit)

    def test_audit_log_records_the_real_reason(self):
        """La causa reale non va persa: deve finire nell'audit log locale."""
        audit = self._audit(central_request("q14_L6", disease=ALS), level=2)
        self.assertIn("disclosure-refused", audit,
                      f"rifiuto non registrato nell'audit log: {audit}")
        self.assertIn("requested=6", audit)
        self.assertIn("local_max=2", audit)
