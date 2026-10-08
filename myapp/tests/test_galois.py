"""Modalita' Galois: schema, mapping fisso, aggiornamento dall'LLM, integrazione."""
import json
import os
import stat
from unittest import mock

import duckdb
from django.test import Client, SimpleTestCase, TestCase

from myapp import catalog, ontology, ontop_process
from myapp.galois import llm, schema, store
from myapp.galois.llm import LLMResponse

from .hdn_helpers import StateDirMixin


class FakeLLM:
    """Risponde con righe preparate; registra i prompt."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.prompts = []

    def chat(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        page = self.pages.pop(0) if self.pages else []
        return LLMResponse(json.dumps(page), usage_tokens=10, latency_s=0.0)


TRIALS = [
    {"nct_id": "NCT01234567", "title": "Trial A", "description": "about ALS",
     "disease_ncit_code": "NCIT:C34373"},
    {"nct_id": "nct01234568", "title": "Trial B", "description": None, "disease_ncit_code": "C34373"},
    {"nct_id": "NCT01234567", "title": "dup", "description": "dup", "disease_ncit_code": "C1"},
    {"nct_id": "not-an-id", "title": "bad key"},
    {"nct_id": "NCT01234569", "title": "bad fk", "disease_ncit_code": "ALS"},
]


def read_parquet(table):
    con = duckdb.connect()
    try:
        cur = con.execute(f"SELECT * FROM read_parquet('{store.parquet_path(table)}') ORDER BY 1")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


class SchemaTests(SimpleTestCase):
    def test_no_patient_terms(self):
        text = schema.obda_text([b for t in schema.TABLES
                                 for b in schema.mapping_blocks(t, [t.key] + [c.name for c in t.columns])])
        self.assertNotIn("Patient", text)
        for bad in ("bto:undergo", "bto:hasDisease", "bto:sex", "Onset", "Event"):
            self.assertNotIn(bad, text)

    def test_full_mapping_valid_for_repository_ontology(self):
        with mock.patch.object(store, "active_tables", lambda cfg=None: [
                (t, [t.key] + [c.name for c in t.columns]) for t in schema.TABLES]):
            text, excluded = store.build_mapping()
        self.assertEqual(excluded, [])
        from myapp.obda_mapping import parse_mappings
        self.assertEqual(len(parse_mappings(text)), sum(1 + len(t.columns) for t in schema.TABLES))

    def test_removed_term_excludes_column_or_table(self):
        g = ontology.parse(open(ontop_process.TTL_FILE, encoding="utf-8").read())
        declared = ontology.declared_terms(g) | ontology.offline_import_terms(g)
        bto = schema.PREFIXES["bto"]
        _, excl = store.build_mapping(declared=declared - {bto + "isAboutDisease"})
        self.assertEqual([m for m, _ in excl], ["GALOIS-clinical_trial-disease_ncit_code"])
        text, excl = store.build_mapping(declared=declared - {bto + "Gene"})
        self.assertEqual([m for m, _ in excl], ["GALOIS-gene"])
        self.assertNotIn("GALOIS-gene-name", text)

    def test_clean_rows(self):
        t = schema.BY_NAME["clinical_trial"]
        cols = ["nct_id", "title", "description", "disease_ncit_code"]
        rows = store.clean_rows(t, cols, TRIALS)
        self.assertEqual([r["nct_id"] for r in rows], ["NCT01234567", "NCT01234568", "NCT01234569"])
        self.assertEqual(rows[0]["disease_ncit_code"], "C34373")
        self.assertIsNone(rows[2]["disease_ncit_code"])

    def test_cleaners_reject_iri_breaking_values(self):
        self.assertIsNone(schema._url("https://x.org/a b"))
        self.assertIsNone(schema._url('https://x.org/a"><evil'))
        self.assertIsNone(schema._atc("A02; DROP"))
        self.assertEqual(schema._atc(" a02ad02 "), "A02AD02")


class TablesForQueryTests(StateDirMixin, SimpleTestCase):
    def tables(self, sparql):
        return store.tables_for_query(sparql, catalog.PREFIXES)

    def test_specific_terms_select_tables(self):
        self.assertEqual(self.tables("ASK { ?t a bto:ClinicalTrial ; bto:isAboutDisease NCIT:C34373 }"),
                         ["clinical_trial"])
        self.assertEqual(self.tables("SELECT ?g WHERE { ?g a bto:Gene }"), ["gene"])

    def test_shared_term_adds_referenced_tables(self):
        q = ("SELECT ?n WHERE { ?t bto:isAboutDisease ?d . "
             "?d <http://www.w3.org/2000/01/rdf-schema#label> ?n }")
        self.assertEqual(self.tables(q), ["disease", "clinical_trial"])

    def test_queries_without_galois_terms_do_not_call_the_llm(self):
        self.assertEqual(self.tables(catalog.BY_KEY["q02_L1"].sparql), [])
        self.assertEqual(self.tables("SELECT * WHERE { ?s ?p ?o }"), [])

    def test_disabled_table_never_refreshed(self):
        cfg = store.load_config()
        cfg["tables"]["gene"]["enabled"] = False
        store.save_config(cfg)
        self.assertEqual(self.tables("SELECT ?g WHERE { ?g a bto:Gene }"), [])


class RefreshTests(StateDirMixin, SimpleTestCase):
    def setUp(self):
        super().setUp()
        store.apply()

    def test_refresh_writes_cleaned_rows_and_status(self):
        fake = FakeLLM([TRIALS, []])
        st = store.refresh_table("clinical_trial", fake)
        self.assertEqual((st["rows"], st["calls"]), (3, 2))
        self.assertIn("select nct_id, title, description, disease_ncit_code from clinical_trial",
                      fake.prompts[0])
        self.assertEqual(fake.prompts[1], "List more values if there are more, otherwise return "
                                          "an empty JSON. Respond with JSON only.")
        self.assertEqual([r["nct_id"] for r in read_parquet("clinical_trial")],
                         ["NCT01234567", "NCT01234568", "NCT01234569"])
        self.assertEqual(store.status()["clinical_trial"]["rows"], 3)

    def test_each_refresh_replaces_previous_rows(self):
        store.refresh_table("clinical_trial", FakeLLM([TRIALS]))
        store.refresh_table("clinical_trial", FakeLLM([[{"nct_id": "NCT99999999"}]]))
        self.assertEqual([r["nct_id"] for r in read_parquet("clinical_trial")], ["NCT99999999"])

    def test_llm_error_keeps_status_and_raises(self):
        class Broken:
            def chat(self, *a, **k):
                raise llm.LLMError("unauthorized", "HTTP 401")
        with self.assertRaises(store.GaloisError):
            store.refresh_table("clinical_trial", Broken())
        self.assertEqual(store.status()["clinical_trial"]["error"], "unauthorized")

    def test_concurrent_request_reuses_fresh_refresh(self):
        import time
        t0 = time.time()
        store.refresh_table("clinical_trial", FakeLLM([TRIALS]))
        fake = FakeLLM([[]])
        store.refresh_table("clinical_trial", fake, not_before=t0)
        self.assertEqual(fake.prompts, [])

    def test_column_change_recreates_view_and_empties_table(self):
        store.refresh_table("clinical_trial", FakeLLM([TRIALS]))
        cfg = store.load_config()
        cfg["tables"]["clinical_trial"]["columns"] = ["title"]
        store.apply(cfg)
        con = duckdb.connect(str(store.database_path()), read_only=True)
        cols = [r[0] for r in con.execute('DESCRIBE "clinical_trial"').fetchall()]
        con.close()
        self.assertEqual(cols, ["nct_id", "title"])
        self.assertEqual(read_parquet("clinical_trial"), [])


class AzureClientTests(SimpleTestCase):
    def call(self, endpoint, status=200, body=None):
        resp = mock.Mock(status_code=status, headers={},
                         text="error", json=lambda: body or {"choices": [{"message": {"content": "[]"}}],
                                                             "usage": {"total_tokens": 7}})
        http = mock.Mock()
        http.post.return_value = resp
        c = llm.AzureOpenAI(endpoint, "dep", "SECRET-KEY", session=http)
        return c, http

    def test_classic_and_v1_urls(self):
        c, http = self.call("https://res.openai.azure.com/")
        self.assertEqual(c.chat([{"role": "user", "content": "x"}]).usage_tokens, 7)
        args, kw = http.post.call_args
        self.assertEqual(args[0], "https://res.openai.azure.com/openai/deployments/dep/chat/completions")
        self.assertEqual(kw["params"], {"api-version": llm.DEFAULT_API_VERSION})
        self.assertEqual(kw["headers"]["api-key"], "SECRET-KEY")
        self.assertEqual(kw["json"]["temperature"], 0)
        c, http = self.call("https://x.services.ai.azure.com/openai/v1/")
        c.chat([{"role": "user", "content": "x"}])
        args, kw = http.post.call_args
        self.assertEqual(args[0], "https://x.services.ai.azure.com/openai/v1/chat/completions")
        self.assertEqual(kw["json"]["model"], "dep")

    def test_errors_never_contain_the_key(self):
        for status, code in ((401, "unauthorized"), (404, "not-found"), (400, "http-error")):
            c, _ = self.call("https://res.openai.azure.com", status=status)
            with self.assertRaises(llm.LLMError) as cm:
                c.chat([{"role": "user", "content": "x"}])
            self.assertEqual(cm.exception.code, code)
            self.assertNotIn("SECRET-KEY", str(cm.exception))


class ProtectedSparqlGaloisTests(StateDirMixin, TestCase):
    QUERY = catalog.PROLOGUE + catalog.BY_KEY["q02_L1"].sparql.replace("{disease}", "NCIT:C34373")

    def setUp(self):
        super().setUp()
        cfg = store.load_config()
        cfg["enabled"] = True
        store.save_config(cfg)
        store.save_azure("https://res.openai.azure.com", "dep", "", "k")
        store.apply(cfg)
        self.client = Client()
        self.ontop = mock.patch("myapp.views.requests.post", return_value=mock.Mock(
            status_code=200, raise_for_status=lambda: None,
            json=lambda: {"head": {"vars": ["nDISEASE"]}, "results": {"bindings": [
                {"nDISEASE": {"type": "literal", "value": "1"}}]}}))
        self.ontop_post = self.ontop.start()
        self.addCleanup(self.ontop.stop)

    def test_query_refreshes_tables_before_ontop(self):
        calls = []
        with mock.patch.object(store, "refresh_for_query",
                               lambda sparql, prefixes: calls.append(sparql) or ["disease"]):
            r = self.client.post("/sparql-protected/", {"query": self.QUERY})
        self.assertEqual(calls, [catalog.BY_KEY["q02_L1"].sparql])
        self.assertEqual(r.json()["results"]["bindings"][0]["nDISEASE"]["value"], "1")
        self.ontop_post.assert_called_once()

    def test_llm_failure_means_no_contribution_and_no_ontop_query(self):
        def fail(*a):
            raise store.GaloisError("unauthorized")
        with mock.patch.object(store, "refresh_for_query", fail), \
                self.assertLogs("hdn.audit", "INFO") as logs:
            r = self.client.post("/sparql-protected/", {"query": self.QUERY})
        self.assertEqual(r.json()["results"]["bindings"], [])
        self.ontop_post.assert_not_called()
        self.assertTrue(any("galois-error" in m for m in logs.output))

    def test_refused_level_does_not_call_the_llm(self):
        q = catalog.PROLOGUE + catalog.BY_KEY["q13_L6"].sparql.replace("{disease}", "NCIT:C34373")
        with mock.patch.object(store, "refresh_for_query") as refresh:
            self.client.post("/sparql-protected/", {"query": q})
        refresh.assert_not_called()

    def test_ontop_uses_galois_mapping_and_properties(self):
        cmd = ontop_process.command()
        self.assertEqual(cmd[cmd.index("-m") + 1], str(store.mapping_path()))
        self.assertEqual(cmd[cmd.index("-p") + 1], str(store.properties_path()))
        props = store.properties_path().read_text()
        self.assertIn(f"jdbc:duckdb:{store.database_path().resolve()}", props)
        self.assertIn("read_only = true", props)


class GaloisPageTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        for fn, ret in (("is_running", False),):
            p = mock.patch.object(ontop_process, fn, lambda *a, _r=ret: _r)
            p.start()
            self.addCleanup(p.stop)

    def form(self, **extra):
        data = {"action": "save", "enabled": "on", "azure_endpoint": "https://res.openai.azure.com/",
                "azure_deployment": "gpt-4o-mini", "azure_api_key": "TOPSECRET", "max_iter": "2"}
        for t in schema.TABLES:
            data[f"table__{t.name}"] = "on"
            for c in t.columns:
                if c.default:
                    data[f"col__{t.name}__{c.name}"] = "on"
        data.update(extra)
        return data

    def test_enable_requires_azure_settings(self):
        r = self.client.post("/galois/", self.form(azure_api_key=""), follow=True)
        self.assertContains(r, "needs the Azure OpenAI endpoint")
        self.assertFalse(store.enabled())

    def test_save_stores_key_privately_and_never_shows_it(self):
        self.client.post("/galois/", self.form())
        self.assertTrue(store.enabled())
        path = store.base_dir() / "azure.json"
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(store.load_azure()["api_key"], "TOPSECRET")
        page = self.client.get("/galois/").content.decode()
        self.assertNotIn("TOPSECRET", page)
        self.assertIn("stored: leave empty to keep it", page)
        self.assertIn("GALOIS-clinical_trial-disease_ncit_code", page)
        # salvataggio senza chiave: quella esistente resta
        self.client.post("/galois/", self.form(azure_api_key=""))
        self.assertEqual(store.load_azure()["api_key"], "TOPSECRET")
        self.assertEqual(store.load_config()["max_iter"], 2)

    def test_key_cannot_be_disabled_and_unknown_columns_ignored(self):
        self.client.post("/galois/", self.form(**{"col__drug__atc_code": "", "col__drug__evil": "on"}))
        cfg = store.load_config()
        self.assertEqual(schema.active_columns(schema.BY_NAME["drug"], cfg["tables"]["drug"])[0], "atc_code")
        self.assertNotIn("evil", cfg["tables"]["drug"]["columns"])

    def test_map_fields_blocked_in_galois_mode(self):
        self.client.post("/galois/", self.form())
        r = self.client.get("/map-fields/")
        self.assertEqual((r.status_code, r["Location"]), (302, "/galois/"))

    def test_galois_page_not_on_8084(self):
        from myproject import sparql_wsgi
        seen = []
        with mock.patch.object(sparql_wsgi, "_django", lambda env, sr: seen.append(1) or [b""]):
            sparql_wsgi.application({"PATH_INFO": "/galois/"}, lambda *a: None)
        self.assertEqual(seen, [])

    def test_saving_with_ontop_running_restarts_it(self):
        calls = []
        with mock.patch.object(ontop_process, "is_running", lambda: True), \
                mock.patch.object(ontop_process, "stop", lambda *a: calls.append("stop") or True), \
                mock.patch.object(ontop_process, "start", lambda *a: calls.append("start") or 1), \
                mock.patch.object(ontop_process, "wait_ready", lambda *a: calls.append("ready") or True):
            self.client.post("/galois/", self.form())
        self.assertEqual(calls, ["stop", "start", "ready"])

