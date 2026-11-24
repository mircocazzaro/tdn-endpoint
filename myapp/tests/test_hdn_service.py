"""hdn-components v1: servizio dell'endpoint su due siti simulati con grafi RDFLib.

Il test fondamentale: due siti soddisfano componenti diverse della stessa
madre; nessuno dei due ha il risultato completo, ma le risposte parziali
permettono a Central di ricostruirlo. L'oracolo e' l'esecuzione della madre
sull'unione dei grafi.
"""

import copy
import json
from collections import Counter

from django.test import SimpleTestCase

from myapp import catalog
from myapp import hdn_components as hc
from myapp import hdn_planner, hdn_service

from .hdn_components_support import (
    evaluate, expected_rows, graph_of, merge_sites, oracle, site_backend,
    write_or_check_fixture,
)

PREFIX = "PREFIX t: <urn:t:>\n"

# Catalogo di prova: due template che restituiscono righe. La madre Compose
# si riscrive con essi; un template nuovo con un predicato nuovo (``t:age``)
# si aggiunge solo qui, senza toccare il codice.
CATALOG_DOC = {
    "version": 1,
    "prologue": "PREFIX t: <urn:t:>\n",
    "templates": [
        {"key": "rows_variant_L1", "level": 1, "description": "patients and variants", "params": {},
         "sparql": "SELECT ?x ?v ?d WHERE { ?x t:variant ?v . ?x t:takes ?d . }"},
        {"key": "rows_age_L1", "level": 1, "description": "patients and age", "params": {},
         "sparql": "SELECT ?x ?a WHERE { ?x t:age ?a . }"},
        {"key": "rows_label_L3", "level": 3, "description": "drug labels", "params": {},
         "sparql": "SELECT ?d ?l WHERE { ?d t:label ?l . }"},
        {"key": "rows_takes_L1", "level": 1, "description": "who takes what", "params": {},
         "sparql": "SELECT ?x ?d WHERE { ?x t:takes ?d . }"},
        # OPTIONAL e' fuori dal frammento Compose: passa solo come istanza di template.
        {"key": "optional_label_L3", "level": 3, "description": "drugs with optional label", "params": {},
         "sparql": "SELECT ?d ?l WHERE { ?p t:takes ?d . OPTIONAL { ?d t:label ?l } }"},
    ],
}

SITE_A = """
@prefix t: <urn:t:> .
<urn:site:A:p17> t:variant <urn:C9orf72> ; t:takes <urn:riluzole> ; t:age 50 .
<urn:site:A:p18> t:variant <urn:TARDBP> ; t:takes <urn:edaravone> ; t:age 60 .
"""
SITE_B = """
@prefix t: <urn:t:> .
<urn:site:B:p5> t:variant <urn:SOD1> ; t:takes <urn:riluzole> ; t:age 70 .
"""

MOTHER = (PREFIX + "SELECT DISTINCT ?d WHERE { ?p1 t:variant <urn:C9orf72> . ?p1 t:takes ?d . "
          "?p2 t:variant <urn:SOD1> . ?p2 t:takes ?d . }")


# Profilo con t:label come livello di riferimento replicato; locali solo takes/variant/age.
REFERENCE_LABEL = hdn_planner.Profile({
    "reference_predicates": ["urn:t:label"],
    "local_positions": [{"predicate": f"urn:t:{p}", "index": 1} for p in ("takes", "variant", "age")],
})


def make_catalog(doc=CATALOG_DOC):
    return catalog.from_document(doc)


def ask_site(query, graph, *, cat=None, level=3, profile=None, calls=None, stamp=None, **kw):
    cat = cat or make_catalog()
    return hdn_service.respond(
        query, cat=cat, profile=profile or hdn_planner.Profile(), ontology_path="/nonexistent",
        local_max_level=level, run=kw.pop("run", None) or site_backend(graph, calls), timeout=5,
        stamp=stamp or (lambda: ()), **kw)


def decode_all(query, outcomes):
    return [hc.decode(o.envelope, query) for o in outcomes]


class TwoSitesTests(SimpleTestCase):
    def setUp(self):
        self.a, self.b = graph_of(SITE_A), graph_of(SITE_B)

    def test_fundamental_partial_responses_rebuild_the_global_result(self):
        # Nessun sito soddisfa tutta la madre...
        self.assertEqual(len(self.a.query(MOTHER)), 0)
        self.assertEqual(len(self.b.query(MOTHER)), 0)
        oa, ob = ask_site(MOTHER, self.a), ask_site(MOTHER, self.b)
        self.assertTrue(oa.contributed and ob.contributed)
        # ...ma ciascuno invia la propria componente e Central ricompone riluzolo.
        da, db = decode_all(MOTHER, [oa, ob])
        self.assertEqual(da.envelope["plan_sha256"], db.envelope["plan_sha256"])
        got = evaluate(MOTHER, da.plan, merge_sites([da, db]))
        self.assertEqual(got, oracle(MOTHER, [self.a, self.b]))
        self.assertEqual(got, Counter({("<urn:riluzole>",): 1}))

    def test_one_site_alone_is_not_enough(self):
        da = decode_all(MOTHER, [ask_site(MOTHER, self.a)])[0]
        self.assertEqual(evaluate(MOTHER, da.plan, merge_sites([da])), Counter())

    def test_the_shared_tuple_is_stored_once_with_two_memberships(self):
        # Una sola tupla [paziente, farmaco] serve piu' componenti se i vettori coincidono.
        q = PREFIX + "SELECT ?d WHERE { ?p t:variant <urn:C9orf72> . ?p t:takes ?d . ?q t:takes ?d . }"
        o = ask_site(q, self.a)
        plan = o.envelope["plan"]
        self.assertEqual(len(plan["components"]), 2)
        pool = o.envelope["tuple_pool"]
        keys = [json.dumps(t, sort_keys=True) for t in pool]
        self.assertEqual(len(keys), len(set(keys)), "ogni vettore compare una volta sola")

    def test_serialized_fixture_for_central(self):
        oa, ob = ask_site(MOTHER, self.a), ask_site(MOTHER, self.b)
        da, db = decode_all(MOTHER, [oa, ob])
        expected = evaluate(MOTHER, da.plan, merge_sites([da, db]))
        write_or_check_fixture(self, "two_sites_c9orf72_sod1", {
            "description": "C9orf72 in A, SOD1 in B, stesso farmaco: nessun sito soddisfa la madre",
            "query": MOTHER,
            "catalog": CATALOG_DOC,
            "profile": hdn_planner.DEFAULT_PROFILE,
            "graphs": {"A": SITE_A, "B": SITE_B},
            "context": oa.envelope["context"],
            "plan": oa.envelope["plan"],
            "plan_sha256": oa.envelope["plan_sha256"],
            "responses": {"A": oa.envelope, "B": ob.envelope},
            "expected": expected_rows(expected),
        })


class DedupAndMultiplicityTests(SimpleTestCase):
    def test_duplicate_rows_are_counted_not_repeated(self):
        q = PREFIX + "SELECT ?x WHERE { ?x t:takes <urn:riluzole> . }"
        graph = graph_of("@prefix t: <urn:t:> . <urn:s:1> t:takes <urn:riluzole> .")

        def run(query, timeout):   # un backend a bag: la stessa soluzione due volte
            data = site_backend(graph)(query, timeout)
            data["results"]["bindings"] = data["results"]["bindings"] * 2
            return data

        o = ask_site(q, graph, run=run)
        self.assertEqual(len(o.envelope["tuple_pool"]), 1)
        self.assertEqual([m["multiplicity"] for m in o.envelope["memberships"]], ["2"])
        self.assertIsInstance(o.envelope["memberships"][0]["multiplicity"], str)

    def test_two_by_three_witnesses_make_six_combinations(self):
        a = graph_of("@prefix t: <urn:t:> . <urn:A:1> t:variant <urn:V1> . <urn:A:1> t:takes <urn:d> . "
                     "<urn:A:2> t:variant <urn:V1> . <urn:A:2> t:takes <urn:d> .")
        b = graph_of("@prefix t: <urn:t:> . "
                     + " ".join(f"<urn:B:{i}> t:variant <urn:V2> . <urn:B:{i}> t:takes <urn:d> ." for i in range(3)))
        q = (PREFIX + "SELECT (COUNT(*) AS ?n) WHERE { ?p1 t:variant <urn:V1> . ?p1 t:takes ?d . "
             "?p2 t:variant <urn:V2> . ?p2 t:takes ?d . }")
        d = decode_all(q, [ask_site(q, a), ask_site(q, b)])
        got = evaluate(q, d[0].plan, merge_sites(d))
        self.assertEqual(got, oracle(q, [a, b]))
        self.assertEqual(list(got), [('"6"^^<http://www.w3.org/2001/XMLSchema#integer>',)])

    def test_replicated_public_fact_is_merged_by_witness(self):
        # La stessa tripla pubblica (nessuna variabile locale) esiste in due siti.
        fact = "@prefix t: <urn:t:> . <urn:drug:riluzole> t:label \"Riluzole\" ."
        a = graph_of(fact + " <urn:A:1> t:takes <urn:drug:riluzole> .")
        b = graph_of(fact + " <urn:B:1> t:takes <urn:drug:riluzole> .")
        q = (PREFIX + "SELECT ?l (COUNT(?p) AS ?n) WHERE { ?p t:takes ?d . ?d t:label ?l . } GROUP BY ?l")
        o = ask_site(q, a)
        modes = {c["id"]: c["merge_mode"] for c in o.envelope["plan"]["components"]}
        # ?d e' soggetto: con il profilo di default e' locale e le due atomi stanno insieme.
        self.assertEqual(set(modes.values()), {"site_unique_bag"})
        # Con t:label predicato di riferimento (livello replicato) l'atomo esce dalla
        # componente di sito e si fonde per testimone.
        shared = REFERENCE_LABEL
        oa = ask_site(q, a, profile=shared)
        ob = ask_site(q, b, profile=shared)
        modes = {c["id"]: c["merge_mode"] for c in oa.envelope["plan"]["components"]}
        self.assertEqual(sorted(modes.values()), ["global_witness_set", "site_unique_bag"])
        d = decode_all(q, [oa, ob])
        got = evaluate(q, d[0].plan, merge_sites(d))
        self.assertEqual(got, oracle(q, [a, b]))       # una sola copia del fatto: n = 2
        self.assertEqual(list(got), [('"Riluzole"', '"2"^^<http://www.w3.org/2001/XMLSchema#integer>')])


class OperatorTests(SimpleTestCase):
    def test_optional_match_found_only_in_the_other_site(self):
        a = graph_of("@prefix t: <urn:t:> . <urn:d:riluzole> t:label \"Riluzole\" . "
                     "<urn:A:1> t:variant <urn:V> .")
        b = graph_of("@prefix t: <urn:t:> . <urn:B:1> t:takes <urn:d:riluzole> .")
        q = (PREFIX + "SELECT ?d ?l WHERE { ?p t:takes ?d . OPTIONAL { ?d t:label ?l } }")
        shared = REFERENCE_LABEL
        d = decode_all(q, [ask_site(q, a, profile=shared), ask_site(q, b, profile=shared)])
        plan = d[0].plan
        self.assertEqual(len(plan["bgps"]), 2, "un BGP per lato dell'OPTIONAL")
        got = evaluate(q, plan, merge_sites(d))
        self.assertEqual(got, oracle(q, [a, b]))
        self.assertEqual(list(got), [("<urn:d:riluzole>", '"Riluzole"')])

    def test_filter_across_components_of_different_sites(self):
        # Esempio della sez. 5.3 del documento: FILTER(?a1 < ?a2) fra due componenti.
        a = graph_of("@prefix t: <urn:t:> . <urn:A:1> t:age 60 .")
        b = graph_of("@prefix t: <urn:t:> . <urn:B:1> t:age 70 .")
        q = PREFIX + "SELECT ?x ?y WHERE { ?x t:age ?a . ?y t:age ?b . FILTER(?a < ?b) }"
        d = decode_all(q, [ask_site(q, a), ask_site(q, b)])
        self.assertEqual([c["vars"] for c in d[0].plan["components"]], [["x", "a"], ["y", "b"]])
        got = evaluate(q, d[0].plan, merge_sites(d))
        self.assertEqual(got, oracle(q, [a, b]))
        self.assertEqual(list(got), [("<urn:A:1>", "<urn:B:1>")])

    def test_projected_away_variables_keep_their_multiplicity(self):
        # Tre pazienti con lo stesso farmaco: si esporta solo ?d, con conteggio 3.
        a = graph_of("@prefix t: <urn:t:> . " + " ".join(f"<urn:A:{i}> t:takes <urn:d> ." for i in range(3)))
        q = PREFIX + "SELECT ?d WHERE { ?p t:takes ?d }"
        o = ask_site(q, a)
        self.assertEqual(o.envelope["plan"]["components"][0]["vars"], ["d"])
        self.assertEqual([m["multiplicity"] for m in o.envelope["memberships"]], ["3"])
        d = decode_all(q, [o])
        self.assertEqual(evaluate(q, d[0].plan, merge_sites(d)), oracle(q, [a]))

    def test_avg_with_sites_of_different_size(self):
        a = graph_of("@prefix t: <urn:t:> . <urn:A:1> t:age 10 . <urn:A:2> t:age 20 . <urn:A:3> t:age 30 .")
        b = graph_of("@prefix t: <urn:t:> . <urn:B:1> t:age 100 .")
        q = PREFIX + "SELECT (AVG(?a) AS ?m) WHERE { ?x t:age ?a . }"
        d = decode_all(q, [ask_site(q, a), ask_site(q, b)])
        got = evaluate(q, d[0].plan, merge_sites(d))
        self.assertEqual(got, oracle(q, [a, b]))      # 40, non la media delle medie (60)

    def test_new_template_with_new_predicate_works_after_catalog_update(self):
        doc = copy.deepcopy(CATALOG_DOC)
        doc["version"] = 2
        doc["templates"].append({"key": "rows_weight_L2", "level": 2, "description": "weight", "params": {},
                                 "sparql": "SELECT ?x ?w WHERE { ?x t:weight ?w . }"})
        q = PREFIX + "SELECT ?x WHERE { ?x t:weight ?w . }"
        g = graph_of("@prefix t: <urn:t:> . <urn:A:1> t:weight 70 .")
        before = ask_site(q, g)
        after = ask_site(q, g, cat=make_catalog(doc))
        self.assertFalse(before.contributed)
        self.assertEqual(before.reason, "no-template-match")
        self.assertTrue(after.contributed)
        self.assertNotEqual(before.envelope["context"]["catalog_sha256"],
                            after.envelope["context"]["catalog_sha256"])
        d = decode_all(q, [after])
        self.assertEqual(evaluate(q, d[0].plan, merge_sites(d)), oracle(q, [g]))
