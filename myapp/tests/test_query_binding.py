"""Audit-test del punto 1: la query eseguita deve essere legata al catalogo.

Prima della correzione ``protected_sparql`` calcolava l'hash del campo
``template`` della richiesta, ne leggeva il livello di disclosure e poi
inoltrava a Ontop il campo ``query``. I due campi erano indipendenti: bastava
dichiarare il testo di un template L0, sempre ammesso, e mettere in ``query``
qualunque SPARQL, ad esempio il dump di tutti i dati dei pazienti. Il livello
massimo configurato dall'istituzione non aveva effetto.

Questi test parlano con l'endpoint solo via HTTP e simulano Ontop, cosi' da
osservare esattamente quale query gli arriva. Usano solo cio' che esisteva gia'
prima della correzione, per poter essere eseguiti anche sul codice precedente.
"""

import json
from unittest import mock

from django.test import Client, TestCase, override_settings

from myapp import catalog
from myapp.tests.helpers import LevelStores, central_request

ALS = "NCIT:C34373"

DUMP_EVERYTHING = catalog.PROLOGUE + (
    "SELECT * WHERE {\n"
    "  ?pat a bto:Patient ;\n"
    "       ?p ?o .\n"
    "}"
)

LEAKED = {
    "head": {"vars": ["pat", "p", "o"]},
    "results": {"bindings": [
        {"pat": {"type": "uri", "value": "urn:LEAKED-PATIENT"}},
    ]},
}

# Valori di esempio, nella forma che il form di Central produce.
SAMPLE_VALUES = {
    "disease": ALS,
    "age": "40",
    "age1": "40",
    "age2": "60",
    "age3": "75",
    "sex": "female",
    "question": "<https://w3id.org/brainteaser/ontology/schema/alsfrs3>",
}


def fake_ontop(result):
    """Sostituisce la chiamata HTTP a Ontop e registra cio' che riceve."""
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = result
    return mock.patch("requests.post", return_value=response)


def sent_queries(ontop):
    return [call.kwargs["data"]["query"] for call in ontop.call_args_list]


def sample_request(template):
    return central_request(template.key, **{p: SAMPLE_VALUES[p] for p in template.params})


class QueryBindingTests(TestCase):

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

    def _post(self, fields, level):
        with override_settings(LEVEL_DB=self.levels[level]):
            return self.client.post("/sparql-protected/", fields)

    def _assert_not_executed(self, fields, level, result=None):
        with fake_ontop(result or LEAKED) as ontop:
            resp = self._post(fields, level)
        self.assertEqual(sent_queries(ontop), [],
                         "la query e' arrivata a Ontop")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"LEAKED", resp.content)
        return resp

    # ------------------------------------------------------------- l'attacco

    def test_l0_signature_cannot_carry_an_arbitrary_query(self):
        """L'attacco del punto 1, su un endpoint configurato al livello minimo.

        Firma valida di q00_L0, query arbitraria che legge tutto.
        """
        resp = self._assert_not_executed({
            "template": catalog.central_mask(catalog.BY_KEY["q00_L0"].sparql),
            "query": DUMP_EVERYTHING,
        }, level=0)
        self.assertEqual(json.loads(resp.content),
                         {"head": {"vars": ["pat", "p", "o"]}, "results": {"bindings": []}})

    def test_l0_signature_cannot_carry_an_l6_catalog_query(self):
        """Stesso attacco con una query che e' nel catalogo, ma a L6."""
        self._assert_not_executed({
            "template": catalog.central_mask(catalog.BY_KEY["q00_L0"].sparql),
            "query": central_request("q14_L6", disease=ALS)["query"],
        }, level=0)

    # ------------------------------------------------- iniezione nei parametri

    def test_disease_value_cannot_extend_the_pattern(self):
        """Il valore di {disease} aggiunge un vincolo sul sesso: una ASK ammessa
        a L0 diventerebbe un oracolo su attributi dei pazienti."""
        self._assert_not_executed(
            central_request("q00_L0", disease='NCIT:C34373 ;\n       bto:sex "female"'),
            level=0, result={"head": {}, "boolean": True})

    def test_sex_value_cannot_escape_its_string_literal(self):
        self._assert_not_executed(
            central_request("q03_L1", disease=ALS, sex='female" ; bto:undergo ?e . ?e ?p "x'),
            level=6)

    def test_age_value_cannot_rewrite_the_filter(self):
        self._assert_not_executed(
            central_request("q01_L0", disease=ALS, age="40 || true"),
            level=0, result={"head": {}, "boolean": True})

    def test_question_value_must_be_one_of_the_alsfrs_questions(self):
        self._assert_not_executed(
            central_request("q08_L3", question="?quest || true"),
            level=6)

    def test_repeated_placeholders_must_carry_the_same_value(self):
        """In q07_L3 {age1} compare tre volte: tutte devono avere lo stesso valore."""
        fields = central_request("q07_L3", disease=ALS, age1="40", age2="60", age3="75")
        tampered = fields["query"].replace('"<40"', '"<99"', 1)
        self.assertNotEqual(tampered, fields["query"])
        self._assert_not_executed({**fields, "query": tampered}, level=6)

    # -------------------------------------------------------------- prologo

    def test_prefixes_cannot_be_redefined(self):
        fields = central_request("q02_L1", disease=ALS)
        redefined = fields["query"].replace(
            "<https://w3id.org/brainteaser/ontology/schema/>", "<https://example.org/>", 1)
        self._assert_not_executed({**fields, "query": redefined}, level=6)

    def test_unknown_prefixes_are_rejected(self):
        fields = central_request("q02_L1", disease=ALS)
        extra = "PREFIX ex: <https://example.org/>\n" + fields["query"]
        self._assert_not_executed({**fields, "query": extra}, level=6)

    def test_query_without_prologue_is_not_executed(self):
        """Senza prologo bto: non e' dichiarato: la query non e' quella del catalogo."""
        fields = central_request("q02_L1", disease=ALS)
        body = fields["query"][len(catalog.PROLOGUE):]
        with fake_ontop(LEAKED) as ontop:
            self._post({**fields, "query": body}, level=6)
        for sent in sent_queries(ontop):
            self.assertTrue(sent.startswith(catalog.PROLOGUE))

    def test_oversized_query_is_rejected(self):
        """Il limite vale anche per spazi interni, che il confronto tollererebbe.

        Spazi in coda verrebbero tolti dallo strip della richiesta: il padding
        va dentro la query, dove senza limite la normalizzazione lo
        accetterebbe e il confronto lavorerebbe su un input arbitrariamente
        grande.
        """
        fields = central_request("q02_L1", disease=ALS)
        padded = fields["query"].replace("WHERE {", "WHERE {" + " " * (70 * 1024), 1)
        self._assert_not_executed({**fields, "query": padded}, level=6)

    # ----------------------------------------------------- uso legittimo

    def test_every_catalog_template_sent_by_central_is_executed_verbatim(self):
        """Il controllo negativo: le richieste di Central passano tutte.

        Per ogni template, istanziato esattamente come fa Central, l'endpoint
        deve inoltrare a Ontop una query identica byte per byte a quella che
        Central ha costruito.
        """
        for template in catalog.CATALOG:
            fields = sample_request(template)
            with self.subTest(template=template.key):
                with fake_ontop({"head": {"vars": []}, "results": {"bindings": []}}) as ontop:
                    resp = self._post(fields, level=6)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(sent_queries(ontop), [fields["query"]])

    def test_the_level_used_is_the_one_of_the_executed_query(self):
        """Il livello e' quello della query, qualunque template si dichiari.

        q02_L1 su un endpoint L1 passa anche se si dichiara q14_L6; q14_L6 su
        un endpoint L1 non passa anche se si dichiara q00_L0.
        """
        q02 = central_request("q02_L1", disease=ALS)
        q14 = central_request("q14_L6", disease=ALS)
        q00_claim = catalog.central_mask(catalog.BY_KEY["q00_L0"].sparql)

        with fake_ontop({"head": {"vars": []}, "results": {"bindings": []}}) as ontop:
            self._post({"template": q14["template"], "query": q02["query"]}, level=1)
        self.assertEqual(sent_queries(ontop), [q02["query"]])

        self._assert_not_executed({"template": q00_claim, "query": q14["query"]}, level=1)

    def test_template_field_is_no_longer_required(self):
        fields = central_request("q02_L1", disease=ALS)
        with fake_ontop({"head": {"vars": []}, "results": {"bindings": []}}) as ontop:
            self._post({"query": fields["query"]}, level=1)
        self.assertEqual(sent_queries(ontop), [fields["query"]])

    def test_formatting_differences_do_not_reach_ontop(self):
        """CRLF e rientri diversi vengono riconosciuti, ma a Ontop arriva il
        testo del catalogo."""
        fields = central_request("q02_L1", disease=ALS)
        reformatted = fields["query"].replace("\n", "\r\n").replace("  ", "\t")
        with fake_ontop({"head": {"vars": []}, "results": {"bindings": []}}) as ontop:
            self._post({**fields, "query": reformatted}, level=1)
        self.assertEqual(sent_queries(ontop), [fields["query"]])

    def test_analytics_path_executes_the_catalog_query(self):
        fields = central_request("q11_L5", disease=ALS)
        with fake_ontop(LEAKED) as ontop:
            self._post({"template": fields["template"], "query": DUMP_EVERYTHING,
                        "analytics_key": "klDiv"}, level=6)
        self.assertEqual(sent_queries(ontop), [],
                         "il ramo analytics ha eseguito una query fuori catalogo")
