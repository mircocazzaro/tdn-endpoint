"""hdn-components v1: contratto (id, digest, decoder), planner e risposte negative."""

import copy
import json
import re
from pathlib import Path

from django.test import SimpleTestCase

from myapp import hdn_components as hc
from myapp import hdn_planner, hdn_service

from .hdn_components_support import graph_of, site_backend
from .test_hdn_service import CATALOG_DOC, MOTHER, PREFIX, SITE_A, ask_site, make_catalog

SPEC = Path(__file__).resolve().parents[2] / "HDN_protocollo_componenti_v1.md"
EXAMPLE_QUERY = ("SELECT DISTINCT ?d WHERE { ?p1 <urn:variant> <urn:C9orf72> . ?p1 <urn:takes> ?d . "
                 "?p2 <urn:variant> <urn:SOD1> . ?p2 <urn:takes> ?d . }")
PARTITION = lambda bid, atoms: [([0, 1], "site_unique_bag"), ([2, 3], "site_unique_bag")]  # noqa: E731


def spec_example():
    """L'involucro completo riportato nel documento del protocollo."""
    text = SPEC.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
    return next(json.loads(b) for b in blocks if '"protocol"' in b)


class ContractTests(SimpleTestCase):
    def test_spec_example_is_reproduced_byte_for_byte(self):
        if not SPEC.is_file():
            self.skipTest("documento del protocollo non presente")
        example = spec_example()
        plan = hc.build_plan(EXAMPLE_QUERY, PARTITION)
        self.assertEqual(plan, example["plan"])
        self.assertEqual(hc.plan_digest(plan), example["plan_sha256"])
        self.assertEqual(hc.query_digest(EXAMPLE_QUERY), example["query_sha256"])
        decoded = hc.decode(example, EXAMPLE_QUERY, expected_context=example["context"])
        self.assertEqual(set(decoded.relation("b0.c0")), set(decoded.relation("b0.c1")))

    def test_query_digest_uses_the_stripped_text(self):
        self.assertEqual(hc.query_digest("  ASK {}\n"), hc.query_digest("ASK {}"))

    def test_plan_digest_is_compact_sorted_utf8(self):
        self.assertEqual(hc.canonical_json({"b": "è", "a": [1]}), '{"a":[1],"b":"è"}')

    def test_subquery_and_optional_keep_their_own_bgps(self):
        q = ("SELECT ?x ?n WHERE { { SELECT ?x (COUNT(?y) AS ?n) WHERE { ?x <urn:a> ?y } GROUP BY ?x } "
             "OPTIONAL { ?x <urn:b> ?z } }")
        plan = hc.build_plan(q, lambda b, a: [([i for i in range(len(a))], "site_unique_bag")])
        self.assertEqual([b["id"] for b in plan["bgps"]], ["b0", "b1"])
        algebra = hc.algebra_of(q)
        for b in plan["bgps"]:
            self.assertEqual(hc.bgp_atoms(hc.node_at(algebra, b["path"]), b["id"]), b["atoms"])

    def test_repeated_atoms_are_kept(self):
        plan = hc.build_plan("SELECT * WHERE { ?x <urn:a> ?y . ?x <urn:a> ?y }",
                             lambda b, a: [([0], "site_unique_bag"), ([1], "site_unique_bag")])
        self.assertEqual(len(plan["bgps"][0]["atoms"]), 2)
        self.assertEqual(len(plan["components"]), 2)

    def test_partition_must_cover_atoms_exactly_once(self):
        with self.assertRaises(ValueError):
            hc.build_plan(EXAMPLE_QUERY, lambda b, a: [([0, 1], "site_unique_bag")])

    def test_literal_terms_keep_datatype_and_language(self):
        plan = hc.build_plan('SELECT * WHERE { ?x <urn:a> "ciao"@it . ?x <urn:b> 5 }',
                             lambda b, a: [([0, 1], "site_unique_bag")])
        pats = [a["pattern"][2] for a in plan["bgps"][0]["atoms"]]
        self.assertIn({"type": "literal", "value": "ciao", "xml:lang": "it"}, pats)
        self.assertIn({"type": "literal", "value": "5",
                       "datatype": "http://www.w3.org/2001/XMLSchema#integer"}, pats)


class UnplannableTests(SimpleTestCase):
    def check(self, q):
        with self.assertRaises(hc.Unplannable):
            hc.build_plan(q, PARTITION)

    def test_exists_is_outside_the_fragment(self):
        self.check("SELECT ?x WHERE { ?x <urn:a> ?y FILTER EXISTS { ?x <urn:b> ?z } }")

    def test_property_path_is_outside_the_fragment(self):
        self.check("SELECT ?x WHERE { ?x <urn:a>/<urn:b> ?y }")

    def test_blank_node_pattern_is_outside_the_fragment(self):
        self.check("SELECT ?x WHERE { ?x <urn:a> [ <urn:b> ?y ] }")

    def test_syntax_error_and_non_select_forms(self):
        self.check("SELECT ?x WHERE { ?x <urn:a> ")
        self.check("CONSTRUCT { ?x <urn:a> ?y } WHERE { ?x <urn:a> ?y }")


class DecoderRejectionTests(SimpleTestCase):
    def setUp(self):
        self.q = MOTHER
        self.resp = ask_site(MOTHER, graph_of(SITE_A)).envelope

    def bad(self, mutate):
        r = copy.deepcopy(self.resp)
        mutate(r)
        with self.assertRaises(hc.InvalidResponse):
            hc.decode(r, self.q)

    def test_valid_response_decodes(self):
        d = hc.decode(self.resp, self.q)
        self.assertEqual(len(d.plan["components"]), 2)

    def test_wrong_protocol_version_and_query_digest(self):
        self.bad(lambda r: r.update(protocol="other"))
        self.bad(lambda r: r.update(version=2))
        self.bad(lambda r: r.update(version=True))
        self.bad(lambda r: r.update(query_sha256="0" * 64))

    def test_context_and_plan_digest(self):
        self.bad(lambda r: r["context"].update(catalog_sha256="zz"))
        self.bad(lambda r: r["context"].update(planner_version="2"))
        self.bad(lambda r: r["plan"].update(evaluation_query=r["plan"]["evaluation_query"] + " "))
        self.bad(lambda r: r.update(plan_sha256="0" * 64))

    def test_tampered_plan_with_recomputed_digest_is_still_rejected(self):
        def mutate(r):
            r["plan"]["components"].pop()
            r["plan_sha256"] = hc.plan_digest(r["plan"])
        self.bad(mutate)

        def mutate_path(r):
            r["plan"]["bgps"][0]["path"] = ["p"]
            r["plan_sha256"] = hc.plan_digest(r["plan"])
        self.bad(mutate_path)

    def test_expected_context_and_peer_plan_must_agree(self):
        other = dict(self.resp["context"], profile_sha256="1" * 64)
        with self.assertRaises(hc.InvalidResponse):
            hc.decode(self.resp, self.q, expected_context=other)
        with self.assertRaises(hc.InvalidResponse):
            hc.decode(self.resp, self.q, expected_plan_sha256="2" * 64)

    def test_references_and_counts(self):
        self.assertTrue(self.resp["memberships"])
        self.bad(lambda r: r["memberships"][0].update(tuple_index=99))
        self.bad(lambda r: r["memberships"][0].update(tuple_index=-1))
        self.bad(lambda r: r["memberships"][0].update(column_indices=[0, 0]))
        self.bad(lambda r: r["memberships"][0].update(column_indices=[9]))
        self.bad(lambda r: r["memberships"][0].update(component_id="b9.c9"))
        for value in ("0", "-1", "1.5", 2, "01", ""):
            self.bad(lambda r, v=value: r["memberships"][0].update(multiplicity=v))

    def test_null_plan_response_carries_nothing(self):
        env = hc.envelope("SELECT ?x WHERE { ?x <urn:a>/<urn:b> ?y }", self.resp["context"], None)
        d = hc.decode(env, "SELECT ?x WHERE { ?x <urn:a>/<urn:b> ?y }")
        self.assertIsNone(d.plan)
        env["tuple_pool"] = [[]]
        with self.assertRaises(hc.InvalidResponse):
            hc.decode(env, "SELECT ?x WHERE { ?x <urn:a>/<urn:b> ?y }")


class PlannerTests(SimpleTestCase):
    def plan(self, q, profile=None):
        return hdn_planner.plan_query(q, profile or hdn_planner.Profile())

    def test_patient_variable_keeps_conditions_together(self):
        plan = self.plan(MOTHER)
        self.assertEqual([c["atom_ids"] for c in plan["components"]],
                         [["b0.a0", "b0.a1"], ["b0.a2", "b0.a3"]])

    def test_shared_variable_alone_does_not_group(self):
        # ?d compare solo come oggetto: due pazienti diversi, componenti diverse.
        q = PREFIX + "SELECT ?d WHERE { ?p1 t:takes ?d . ?p2 t:takes ?d }"
        self.assertEqual(len(self.plan(q)["components"]), 2)

    def test_component_without_variables_gets_a_count_query(self):
        q = "SELECT ?x WHERE { <urn:s> <urn:p> <urn:o> . ?x <urn:q> <urn:o> }"
        plan = self.plan(q)
        queries = [hdn_planner.component_query(plan, c) for c in plan["components"]]
        self.assertTrue(any(s.startswith("SELECT (COUNT(*) AS ?n)") for s in queries))

    def test_local_query_is_rebuilt_from_terms_not_from_the_mother_text(self):
        q = PREFIX + 'SELECT ?x WHERE { ?x t:name "a\\"b"@en . }'
        plan = self.plan(q)
        text = hdn_planner.component_query(plan, plan["components"][0])
        self.assertIn('"a\\"b"@en', text)
        self.assertNotIn("PREFIX", text)

    def test_profile_digest_changes_with_the_profile(self):
        other = hdn_planner.Profile({"local_positions": []})
        self.assertNotEqual(hdn_planner.Profile().sha256, other.sha256)

    def test_invalid_profile_rule_is_refused(self):
        with self.assertRaises(ValueError):
            hdn_planner.Profile({"local_positions": [{"predicate": "*", "index": 3}]})

    def test_only_interface_variables_are_exported_with_counts(self):
        # ?p non serve fuori dalla componente: si esporta solo ?d (testa e join).
        plan = self.plan(PREFIX + "SELECT ?d WHERE { ?p1 t:takes ?d . ?p2 t:takes ?d }")
        self.assertEqual([c["vars"] for c in plan["components"]], [["d"], ["d"]])
        # Con SELECT * la testa le comprende tutte.
        plan = self.plan(PREFIX + "SELECT * WHERE { ?p1 t:takes ?d . ?p2 t:takes ?d }")
        self.assertEqual([c["vars"] for c in plan["components"]], [["p1", "d"], ["p2", "d"]])

    def test_variables_used_by_filters_orderings_and_other_bgps_are_exported(self):
        plan = self.plan(PREFIX + "SELECT ?x WHERE { ?x t:age ?a . ?y t:age ?b . FILTER(?a < ?b) }")
        self.assertEqual([c["vars"] for c in plan["components"]], [["x", "a"], ["b"]])
        plan = self.plan(PREFIX + "SELECT ?x WHERE { ?x t:age ?a } ORDER BY ?a")
        self.assertEqual(plan["components"][0]["vars"], ["x", "a"])
        plan = self.plan(PREFIX + "SELECT ?x WHERE { ?x t:takes ?d OPTIONAL { ?y t:label ?d } }")
        self.assertEqual(plan["components"][0]["vars"], ["x", "d"])

    def test_reference_atoms_form_their_own_witness_components(self):
        # q5 del documento: diag(p, d), broader(d, MND).
        profile = hdn_planner.Profile({"reference_predicates": ["urn:t:broader"],
                                       "local_positions": [{"predicate": "urn:t:diag", "index": 1}]})
        plan = self.plan(PREFIX + "SELECT ?p ?d WHERE { ?p t:diag ?d . ?d t:broader <urn:MND> }", profile)
        modes = {tuple(c["atom_ids"]): c["merge_mode"] for c in plan["components"]}
        self.assertEqual(sorted(modes.values()), ["global_witness_set", "site_unique_bag"])
        ref = next(c for c in plan["components"] if c["merge_mode"] == "global_witness_set")
        self.assertEqual(ref["vars"], ["d"])

    def test_local_constants_of_the_same_owner_share_a_component(self):
        profile = hdn_planner.Profile({
            "local_positions": [{"predicate": "urn:t:takes", "index": 1}],
            "site_prefixes": [{"site": 1, "prefix": "urn:A:"}, {"site": 2, "prefix": "urn:B:"}]})
        same = self.plan(PREFIX + "ASK { <urn:A:1> t:takes ?d . <urn:A:2> t:takes ?e }", profile)
        self.assertEqual(len(same["components"]), 1)
        different = self.plan(PREFIX + "ASK { <urn:A:1> t:takes ?d . <urn:B:2> t:takes ?e }", profile)
        self.assertEqual(len(different["components"]), 2)
        self.assertEqual({c["merge_mode"] for c in different["components"]}, {"site_unique_bag"})

    def test_an_atom_without_anchor_is_a_component_by_itself(self):
        profile = hdn_planner.Profile({"local_positions": []})
        plan = self.plan(PREFIX + "SELECT ?a ?b WHERE { ?a t:near ?c . ?c t:near ?b }", profile)
        self.assertEqual(len(plan["components"]), 2)
        self.assertEqual({c["merge_mode"] for c in plan["components"]}, {"global_witness_set"})


class NegativeResponsesTests(SimpleTestCase):
    """Rifiuto, assenza di match e guasto: stessa struttura pubblica."""

    def setUp(self):
        self.g = graph_of(SITE_A)

    def test_rejected_unmatched_and_failed_are_indistinguishable(self):
        # Stessa madre: rifiutata per livello, fuori catalogo (catalogo vuoto di template
        # utili), backend guasto. Le tre risposte devono coincidere.
        refused = ask_site(MOTHER, self.g, level=0)
        failing = ask_site(MOTHER, self.g, run=self._raises)
        empty_catalog = make_catalog({"version": 1, "prologue": CATALOG_DOC["prologue"],
                                      "templates": [CATALOG_DOC["templates"][1]]})
        unmatched = ask_site(MOTHER, self.g, cat=empty_catalog)
        for o in (refused, failing):
            self.assertFalse(o.contributed)
        self.assertEqual(refused.reason, "disclosure-refused")
        self.assertEqual(failing.reason, "backend-error")
        self.assertEqual(unmatched.reason, "no-template-match")
        # Il catalogo differente cambia il contesto, come deve; il resto coincide.
        self.assertEqual(refused.envelope, failing.envelope)
        a, b = dict(unmatched.envelope), dict(refused.envelope)
        a.pop("context"), b.pop("context")
        self.assertEqual(a, b)
        self.assertEqual(refused.envelope["tuple_pool"], [])
        self.assertEqual(refused.envelope["memberships"], [])
        self.assertIsNotNone(refused.envelope["plan"])

    def test_failure_in_the_middle_gives_the_canonical_response(self):
        calls = []
        base = site_backend(self.g)

        def run(query, timeout):
            calls.append(query)
            if len(calls) == 2:
                raise ConnectionError("backend down")
            return base(query, timeout)

        o = ask_site(MOTHER, self.g, run=run)
        self.assertEqual(len(calls), 2)
        self.assertFalse(o.contributed)
        self.assertEqual(o.envelope, ask_site(MOTHER, self.g, level=0).envelope)

    def test_unplannable_query_uses_null_plan_for_every_outcome(self):
        q = PREFIX + "SELECT ?x WHERE { ?x t:takes/t:variant ?y }"
        a = ask_site(q, self.g, level=0).envelope
        b = ask_site(q, self.g, run=self._raises).envelope
        self.assertIsNone(a["plan"])
        self.assertIsNone(a["plan_sha256"])
        self.assertEqual(a, b)
        self.assertEqual((a["tuple_pool"], a["memberships"]), ([], []))

    def test_state_changed_during_execution_gives_no_contribution(self):
        stamps = iter([("a",), ("b",)])
        o = ask_site(MOTHER, self.g, stamp=lambda: next(stamps))
        self.assertFalse(o.contributed)
        self.assertEqual(o.reason, "state-changed")
        self.assertEqual(o.envelope["memberships"], [])

    def test_row_limit_is_a_failure_not_a_truncation(self):
        o = ask_site(MOTHER, self.g, max_rows=0)
        self.assertFalse(o.contributed)
        self.assertEqual(o.reason, "row-limit")

    def test_blank_nodes_in_results_are_not_exported(self):
        def run(query, timeout):
            return {"head": {"vars": ["x"]},
                    "results": {"bindings": [{"x": {"type": "bnode", "value": "b1"}}]}}
        q = PREFIX + "SELECT ?x WHERE { ?x t:age ?a }"
        o = ask_site(q, self.g, run=run)
        self.assertEqual(o.reason, "blank-node")

    def test_malformed_backend_answer_is_a_failure(self):
        o = ask_site(MOTHER, self.g, run=lambda q, t: {"boolean": True})
        self.assertEqual(o.reason, "backend-malformed")

    def test_ambiguous_templates_keep_the_most_restrictive_level(self):
        doc = copy.deepcopy(CATALOG_DOC)
        doc["templates"].append(dict(doc["templates"][1], key="rows_age_L5", level=5,
                                     sparql="SELECT ?x ?a WHERE {  ?x t:age ?a . }"))
        q = "SELECT ?x ?a WHERE { ?x t:age ?a . }"
        auth = hdn_service.authorize(PREFIX + q, make_catalog(doc))
        self.assertEqual((auth.kind, auth.level, auth.key), ("template", 5, "rows_age_L5"))
        self.assertEqual(auth.alternatives, ("rows_age_L1",))

    def test_compose_level_is_the_maximum_of_the_plan_templates(self):
        auth = hdn_service.authorize(PREFIX + "SELECT ?x ?l WHERE { ?x t:takes ?d . ?d t:label ?l }",
                                     make_catalog())
        self.assertEqual((auth.kind, auth.level), ("compose", 3))
        self.assertEqual(auth.key, "rows_label_L3,rows_takes_L1")

    def test_compose_with_unsupported_operator_is_refused(self):
        with self.assertRaises(hdn_service.NotAuthorized):
            hdn_service.authorize(PREFIX + "SELECT ?x WHERE { ?x t:takes ?d . OPTIONAL { ?x t:age ?a } }",
                                  make_catalog())

    @staticmethod
    def _raises(query, timeout):
        raise ConnectionError("backend down")
