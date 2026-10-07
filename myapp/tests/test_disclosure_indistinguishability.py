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

from django.test import Client, TestCase, override_settings

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
        self.client = Client()
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

    def test_ask_refusal_has_canonical_ask_shape(self):
        """Un rifiuto su ASK deve avere la forma di una ASK negativa."""
        resp = self._post({
            "template": "ASK WHERE { ?s ?p ?o }",
            "query": catalog.PROLOGUE + "ASK WHERE { ?s ?p ?o }",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), {"head": {}, "boolean": False})

    def test_select_refusal_declares_the_projected_variables(self):
        """Una SELECT vuota deve dichiarare le variabili proiettate.

        Senza questo, Central distingue il rifiuto da un risultato vuoto
        semplicemente guardando se ``head.vars`` c'e' o no.
        """
        resp = self._post(central_request("q14_L6", disease=ALS))  # L6 su L2
        self.assertEqual(json.loads(resp.content), {
            "head": {"vars": ["pat", "name", "aOns", "sex", "ev", "evType", "evStart"]},
            "results": {"bindings": []},
        })

    # -------------------------------------------------------- indistinguibilita'

    def test_all_negative_outcomes_are_byte_identical(self):
        """La proprieta' centrale del punto 5.

        Tre cause diverse - template ignoto, rifiuto per livello, backend
        irraggiungibile - devono produrre la stessa risposta byte per byte e
        lo stesso stato HTTP. Le tre query hanno la stessa proiezione, cosi'
        che l'unica differenza possibile sia la causa.
        """
        q02 = central_request("q02_L1", disease=ALS)

        responses = {
            # (a) query e template fuori catalogo
            "unknown-template": self._post({
                "template": "SELECT ?x WHERE { ?x a <urn:NonInCatalogo> }",
                "query": UNKNOWN_COUNT_QUERY,
            }),
            # (b) template L1 su un endpoint configurato a L0
            "disclosure-refused": self._post(q02, level=0),
            # (c) template ammesso a L2, ma il backend Ontop non risponde
            "backend-error": self._post(q02, level=2),
        }

        for name, resp in responses.items():
            with self.subTest(outcome=name):
                self.assertEqual(resp.status_code, 200,
                                 f"{name}: lo stato HTTP rivela la causa")
                self.assertNotIn(b"error", resp.content.lower(),
                                 f"{name}: il corpo rivela stato interno")

        bodies = {name: resp.content for name, resp in responses.items()}
        self.assertEqual(len(set(bodies.values())), 1,
                         "gli esiti negativi sono distinguibili: " + repr(bodies))

    def test_refusal_is_identical_to_a_genuinely_empty_answer(self):
        """Un rifiuto deve coincidere con cio' che Ontop risponderebbe se non
        ci fossero pazienti che matchano."""
        refused = self._post(central_request("q02_L1", disease=ALS), level=0)
        genuine_empty = {"head": {"vars": ["nDISEASE"]}, "results": {"bindings": []}}
        self.assertEqual(json.loads(refused.content), genuine_empty)

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
