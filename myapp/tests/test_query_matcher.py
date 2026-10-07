"""Test unitari di catalog.match_query, la funzione che lega una query al catalogo."""

import dataclasses
import hashlib
from unittest import mock

from django.test import SimpleTestCase

from myapp import catalog
from myapp.tests.helpers import central_request

ALS = "NCIT:C34373"


def _synthetic(key, level, sparql):
    return catalog.Template(
        key=key, level=level, description="", params={},
        sha512=hashlib.sha512(sparql.encode("utf-8")).hexdigest(), sparql=sparql)


class MatchQueryTests(SimpleTestCase):

    def test_extracts_template_and_bindings(self):
        fields = central_request("q07_L3", disease=ALS, age1="40", age2="60", age3="75")
        match = catalog.match_query(fields["query"])
        self.assertEqual(match.template.key, "q07_L3")
        self.assertEqual(dict(match.bindings),
                         {"disease": ALS, "age1": "40", "age2": "60", "age3": "75"})
        self.assertEqual(match.query, fields["query"])

    def test_template_without_parameters(self):
        match = catalog.match_query(central_request("q06_L3")["query"])
        self.assertEqual(match.template.key, "q06_L3")
        self.assertEqual(dict(match.bindings), {})

    def test_comment_in_template_does_not_hide_the_filter(self):
        """q08_L3 ha un commento '#' prima del FILTER.

        Il confronto normalizza gli spazi, quindi una query che mette il FILTER
        sulla stessa riga del commento, disattivandolo, viene riconosciuta: ma
        cio' che si esegue e' il testo del catalogo, dove il FILTER e' attivo.
        """
        q = "<https://w3id.org/brainteaser/ontology/schema/alsfrs3>"
        fields = central_request("q08_L3", question=q)
        commented_out = fields["query"].replace(
            "goes here\n      FILTER", "goes here FILTER", 1)
        self.assertNotEqual(commented_out, fields["query"])

        match = catalog.match_query(commented_out)
        self.assertEqual(match.query, fields["query"])
        self.assertIn("goes here\n      FILTER (?quest = " + q + ")", match.query)

    def test_rejection_reasons(self):
        q02 = central_request("q02_L1", disease=ALS)["query"]
        cases = {
            "no-template-match": q02.replace("COUNT(DISTINCT", "COUNT("),
            "prefix-redefined": q02.replace(
                "<http://purl.obolibrary.org/obo/NCIT_>", "<urn:x:>", 1),
            "prefix-not-allowed": "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n" + q02,
            "query-too-large": q02 + " " * catalog.MAX_QUERY_CHARS,
        }
        for reason, query in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaises(catalog.RejectedQuery) as ctx:
                    catalog.match_query(query)
                self.assertEqual(ctx.exception.reason, reason)

    def test_ambiguous_match_resolves_to_the_highest_level(self):
        """Se due template riconoscono la stessa query vale il piu' restrittivo."""
        text = "ASK WHERE { ?s a bto:Patient . }"
        low = _synthetic("t_low", 0, text)
        high = _synthetic("t_high", 5, text)
        compiled = ((low, catalog._compile(low)), (high, catalog._compile(high)))

        with mock.patch.object(catalog, "_COMPILED", compiled):
            match = catalog.match_query(catalog.PROLOGUE + text)

        self.assertEqual(match.template.key, "t_high")
        self.assertEqual(match.alternatives, ("t_low",))

    def test_catalog_templates_are_not_ambiguous(self):
        """Nel catalogo reale ogni istanza riconosce solo il proprio template."""
        values = {"disease": ALS, "age": "40", "age1": "40", "age2": "60",
                  "age3": "75", "sex": "female",
                  "question": "<https://w3id.org/brainteaser/ontology/schema/alsfrs3>"}
        for t in catalog.CATALOG:
            fields = central_request(t.key, **{p: values[p] for p in t.params})
            with self.subTest(template=t.key):
                match = catalog.match_query(fields["query"])
                self.assertEqual(match.template.key, t.key)
                self.assertEqual(match.alternatives, ())


class InstantiateTests(SimpleTestCase):

    def test_substitution_is_single_pass(self):
        """Un valore non viene reinterpretato come segnaposto."""
        t = catalog.BY_KEY["q01_L0"]
        replaced = dataclasses.replace(
            t, params={"disease": catalog.ParamType("x", r"\{age\}", ""),
                       "age": t.params["age"]})
        out = catalog.instantiate(replaced, {"disease": "{age}", "age": "40"})
        self.assertIn("bto:hasDisease {age}", out)

    def test_rejects_values_outside_the_grammar(self):
        t = catalog.BY_KEY["q00_L0"]
        with self.assertRaises(ValueError):
            catalog.instantiate(t, {"disease": "?x"})

    def test_rejects_missing_or_extra_parameters(self):
        t = catalog.BY_KEY["q01_L0"]
        with self.assertRaises(ValueError):
            catalog.instantiate(t, {"disease": ALS})
        with self.assertRaises(ValueError):
            catalog.instantiate(t, {"disease": ALS, "age": "40", "sex": "female"})
