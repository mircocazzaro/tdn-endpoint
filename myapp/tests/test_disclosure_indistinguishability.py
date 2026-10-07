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

Questi test verificano la proprieta' che conta davvero: tutti gli esiti
negativi producono *la stessa* risposta, byte per byte.
"""

import hashlib
import json
import tempfile
from pathlib import Path

import duckdb
from django.test import Client, TestCase, override_settings

ASK_TEMPLATE = """ASK WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease {disease} .
}"""

SELECT_TEMPLATE = """SELECT (COUNT(DISTINCT ?pat) AS ?nDISEASE) WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease {disease} .
}"""

L6_TEMPLATE = """SELECT ?pat ?name ?sex WHERE {
  ?pat a bto:Patient ;
       bto:sex ?sex ;
       bto:hasDisease {disease} .
}"""


def _hash(text):
    return hashlib.sha512(text.encode("utf-8")).hexdigest()


def _instantiate(template):
    return template.replace("{disease}", "NCIT:C34373")


class DisclosureIndistinguishabilityTests(TestCase):
    """Ogni mancato contributo deve essere osservazionalmente identico."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.allowed_db = str(tmp / "allowed_queries.duckdb")
        cls.level_db = str(tmp / "level.duckdb")

        con = duckdb.connect(cls.allowed_db)
        con.execute(
            "CREATE TABLE allowed_queries (hash TEXT PRIMARY KEY, level INTEGER, query TEXT)"
        )
        for tmpl, lvl in [(ASK_TEMPLATE, 0), (SELECT_TEMPLATE, 1), (L6_TEMPLATE, 6)]:
            con.execute("INSERT INTO allowed_queries VALUES (?, ?, ?)",
                        [_hash(tmpl), lvl, tmpl])
        con.close()

        con = duckdb.connect(cls.level_db)
        con.execute("CREATE TABLE options (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO options VALUES ('level', 'L2 - Full Aggregations (AVG, ecc.)')")
        con.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.client = Client()
        # ONTOP_SPARQL_ENDPOINT punta a una porta chiusa: le richieste che
        # arrivano al backend falliscono, ed e' esattamente il terzo caso
        # negativo che vogliamo rendere indistinguibile dagli altri due.
        self.overrides = override_settings(
            ALLOWED_DB=self.allowed_db,
            LEVEL_DB=self.level_db,
            ONTOP_SPARQL_ENDPOINT="http://127.0.0.1:1/sparql",
        )
        self.overrides.enable()
        self.addCleanup(self.overrides.disable)

    def _post(self, template, query=None):
        return self.client.post("/sparql-protected/", {
            "template": template,
            "query": query if query is not None else _instantiate(template),
        })

    # ------------------------------------------------------------------ forma

    def test_ask_refusal_has_canonical_ask_shape(self):
        """Un rifiuto su ASK deve avere la forma di una ASK negativa."""
        # L6 su un endpoint configurato a L2 -> rifiutato
        resp = self._post(L6_TEMPLATE)
        self.assertEqual(resp.status_code, 200)

        ask_refused = self.client.post("/sparql-protected/", {
            "template": ASK_TEMPLATE,
            "query": "ASK WHERE { ?s ?p ?o }",
        })
        self.assertEqual(ask_refused.status_code, 200)
        self.assertEqual(json.loads(ask_refused.content),
                         {"head": {}, "boolean": False})

    def test_select_refusal_declares_the_projected_variables(self):
        """Una SELECT vuota deve dichiarare le variabili proiettate.

        Senza questo, Central distingue il rifiuto da un risultato vuoto
        semplicemente guardando se ``head.vars`` c'e' o no.
        """
        resp = self._post(L6_TEMPLATE)
        body = json.loads(resp.content)
        self.assertEqual(body, {"head": {"vars": ["pat", "name", "sex"]},
                                "results": {"bindings": []}})

    # -------------------------------------------------------- indistinguibilita'

    def test_all_negative_outcomes_are_byte_identical(self):
        """La proprieta' centrale del punto 5.

        Tre cause diverse - template ignoto, rifiuto per livello, backend
        irraggiungibile - devono produrre la stessa risposta byte per byte e
        lo stesso stato HTTP.
        """
        query = _instantiate(SELECT_TEMPLATE)

        # (a) template non presente in catalogo
        unknown = self.client.post("/sparql-protected/", {
            "template": "SELECT ?x WHERE { ?x a <urn:NonInCatalogo> }",
            "query": query,
        })

        # (b) template in catalogo ma di livello superiore al massimo locale
        refused = self.client.post("/sparql-protected/", {
            "template": L6_TEMPLATE,
            "query": query,
        })

        # (c) template ammesso, ma il backend Ontop non risponde
        backend_down = self.client.post("/sparql-protected/", {
            "template": SELECT_TEMPLATE,
            "query": query,
        })

        responses = {
            "unknown-template": unknown,
            "disclosure-refused": refused,
            "backend-error": backend_down,
        }

        for name, resp in responses.items():
            with self.subTest(outcome=name):
                self.assertEqual(resp.status_code, 200,
                                 f"{name}: lo stato HTTP rivela la causa")
                self.assertNotIn(b"error", resp.content.lower(),
                                 f"{name}: il corpo rivela stato interno")

        bodies = {name: resp.content for name, resp in responses.items()}
        distinct = set(bodies.values())
        self.assertEqual(
            len(distinct), 1,
            "gli esiti negativi sono distinguibili: " + repr(bodies),
        )

    def test_refusal_is_identical_to_a_genuinely_empty_answer(self):
        """Un rifiuto deve coincidere con cio' che Ontop risponderebbe se non
        ci fossero pazienti che matchano."""
        query = _instantiate(SELECT_TEMPLATE)

        refused = self.client.post("/sparql-protected/", {
            "template": L6_TEMPLATE, "query": query,
        })

        # Risposta vuota genuina per la stessa query, nel formato SPARQL-JSON
        # che l'endpoint inoltra verbatim quando Ontop risponde.
        genuine_empty = json.dumps(
            {"head": {"vars": ["nDISEASE"]}, "results": {"bindings": []}}
        )

        self.assertEqual(json.loads(refused.content), json.loads(genuine_empty))

    def test_level_is_enforced_in_the_right_direction(self):
        """Un template al di sotto del massimo locale non viene rifiutato dal
        controllo di livello.

        Serve come controllo negativo: se questo test passasse anche con il
        livello invertito, il test precedente non proverebbe nulla.
        """
        from myapp import views

        captured = []
        original = views.audit.info
        views.audit.info = lambda msg, *a, **k: captured.append(msg % a if a else msg)
        try:
            self.client.post("/sparql-protected/", {
                "template": SELECT_TEMPLATE,  # L1 <= L2 locale
                "query": _instantiate(SELECT_TEMPLATE),
            })
        finally:
            views.audit.info = original

        joined = " ".join(captured)
        self.assertIn("backend-error", joined,
                      "L1 su endpoint L2 doveva superare il controllo di livello "
                      f"e fallire sul backend; audit: {joined}")
        self.assertNotIn("disclosure-refused", joined)

    def test_audit_log_records_the_real_reason(self):
        """La causa reale non va persa: deve finire nell'audit log locale."""
        from myapp import views

        captured = []
        original = views.audit.info
        views.audit.info = lambda msg, *a, **k: captured.append(msg % a if a else msg)
        try:
            self.client.post("/sparql-protected/", {
                "template": L6_TEMPLATE,
                "query": _instantiate(L6_TEMPLATE),
            })
        finally:
            views.audit.info = original

        self.assertTrue(any("disclosure-refused" in c for c in captured),
                        f"rifiuto non registrato nell'audit log: {captured}")
        self.assertTrue(any("requested=6" in c and "local_max=2" in c
                            for c in captured),
                        f"audit log senza i livelli coinvolti: {captured}")
