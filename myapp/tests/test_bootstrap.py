"""LLM bootstrap of the mapping: prompt, validation, suggestions in Map Data to HERO."""
import json
import re
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.test import Client, TestCase

from myapp import azure_settings, ontop_process, views
from myapp.bootstrap import prompt as P
from myapp.bootstrap import run
from myapp.galois.llm import LLMError, LLMResponse

from .hdn_helpers import StateDirMixin

TEMPLATE = '''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX
target		bto:Patient{patient} a bto:Patient ; bto:sex {sex}^^xsd:string .
source		SELECT patient, sex FROM "PATIENTS GENERAL DATA"

mappingId	MAPID-ALIVE
target		bto:Patient{patient} bto:alive {alive}^^xsd:boolean .
source		SELECT patient, alive FROM "PATIENTS GENERAL DATA" WHERE alive IS NOT NULL

mappingId	MAPID-ONSET
target		bto:Patient{patient} bto:undergo bto:eventOnset1{patient} . bto:eventOnset1{patient} bto:ageOnset {age_onset}^^xsd:float .
source		SELECT patient, age_onset FROM "ONSET"
]]
'''
SECRET = "SECRET-PATIENT-VALUE-42"

# What a good model answers for the local schema below
ANSWERS = {
    "MAPID-SEX": {"table": "anagrafica", "bindings": [
        {"placeholder": "patient", "column": "id_paziente"}, {"placeholder": "sex", "column": "sesso"}]},
    "MAPID-ALIVE": {"table": "anagrafica", "bindings": [
        {"placeholder": "patient", "column": "id_paziente"}, {"placeholder": "alive", "column": None}]},
    "MAPID-ONSET": {"table": "esordio", "bindings": [
        {"placeholder": "patient", "column": "id_paziente"}, {"placeholder": "age_onset", "column": "eta"}]},
}


class FakeAzure:
    """Answers from ANSWERS for the indexes in the prompt; records prompts."""

    def __init__(self, answers=ANSWERS, truncate_over=None, structured=True):
        self.answers = answers
        self.prompts = []
        self.truncate_over = truncate_over
        self.structured = structured

    def chat(self, messages, **kw):
        user = messages[-1]["content"]
        self.prompts.append((user, kw))
        if kw.get("response_format") and not self.structured:
            raise LLMError("http-error", "HTTP 400: response_format not supported")
        rules = re.findall(r"^\[(\d+)\] mappingId=(\S+)", user, re.M)
        if self.truncate_over and len(rules) > self.truncate_over:
            return LLMResponse('{"items": [', 100, 0.1, "length")
        items = [dict(index=int(i), **self.answers[m]) for i, m in rules if m in self.answers]
        return LLMResponse("```json\n" + json.dumps({"items": items}) + "\n```", 50, 0.1, "stop")


class BootstrapTestBase(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        tmp = Path(tempfile.mkdtemp())
        self.db = str(tmp / "site.duckdb")
        con = duckdb.connect(self.db)
        con.execute('CREATE TABLE anagrafica (id_paziente VARCHAR, sesso VARCHAR, alive BOOLEAN, note VARCHAR)')
        con.execute("INSERT INTO anagrafica VALUES ('p1', 'QQSEXVALUE', true, ?)", [SECRET])
        con.execute('CREATE TABLE esordio (id_paziente VARCHAR, eta DOUBLE)')
        con.execute("INSERT INTO esordio VALUES ('p1', 51.37)")
        con.close()
        (tmp / "template.obda").write_text(TEMPLATE)
        self.out = tmp / "active.obda"
        for name, value in [("DUCKDB_PATH", self.db), ("TEMPLATE_OBDA", str(tmp / "template.obda")),
                            ("OBDA_FILE", str(self.out)), ("ONTOP_DIR", str(tmp))]:
            p = mock.patch.object(views, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ontop_process, "is_running", lambda: False)
        p.start()
        self.addCleanup(p.stop)
        self.client = Client()

    def blocks(self):
        from myapp.bootstrap_views import site_schema
        schema = site_schema(self.db)
        header, blocks = views.template_mapping_blocks(
            {t: [c.name for c in tb.columns] for t, tb in schema.tables.items()})
        return header, blocks, schema


class PipelineTests(BootstrapTestBase):
    def test_suggestions_validated_and_snapped(self):
        header, blocks, schema = self.blocks()
        llm = FakeAzure()
        sug, summary = run.bootstrap(llm, header, blocks, schema)
        self.assertEqual(sug["MAPID-SEX"]["pairs"], {"patient": "id_paziente", "sex": "sesso"})
        # alive left null by the model: snapped to the column with the same name
        self.assertEqual(sug["MAPID-ALIVE"]["pairs"], {"patient": "id_paziente", "alive": "alive"})
        self.assertEqual(sug["MAPID-ONSET"]["table"], "esordio")
        self.assertEqual((summary["suggested"], summary["complete"]), (3, 3))
        user, kw = llm.prompts[0]
        self.assertIn('"anagrafica"(id_paziente:VARCHAR, sesso:VARCHAR, alive:BOOLEAN', user)
        self.assertIn("ageOnset (data property)", user)
        self.assertIn("template_sql (reference schema", user)
        self.assertEqual(kw["response_format"]["type"], "json_schema")

    def test_no_data_values_in_the_prompt(self):
        header, blocks, schema = self.blocks()
        llm = FakeAzure()
        run.bootstrap(llm, header, blocks, schema)
        self.assertTrue(llm.prompts)
        self.assertFalse(any(SECRET in u or "QQSEXVALUE" in u or "51.37" in u or "p1" in u.split() for u, _ in llm.prompts))

    def test_invented_tables_and_columns_dropped(self):
        header, blocks, schema = self.blocks()
        bad = dict(ANSWERS)
        bad["MAPID-SEX"] = {"table": "patients", "bindings": [{"placeholder": "patient", "column": "id"}]}
        bad["MAPID-ONSET"] = {"table": "ESORDIO", "bindings": [
            {"placeholder": "patient", "column": "id_paziente"}, {"placeholder": "age_onset", "column": "sesso"},
            {"placeholder": "not_a_placeholder", "column": "eta"}]}
        sug, summary = run.bootstrap(FakeAzure(bad), header, blocks, schema)
        self.assertNotIn("MAPID-SEX", sug)
        self.assertEqual(sug["MAPID-ONSET"], {"table": "esordio", "pairs": {"patient": "id_paziente"},
                                              "notes": ["age_onset: column 'sesso' is not in 'esordio'"],
                                              "complete": False})
        self.assertTrue(any("unknown table 'patients'" in p for p in summary["problems"]))

    def test_truncated_chunk_is_split(self):
        header, blocks, schema = self.blocks()
        llm = FakeAzure(truncate_over=1)
        sug, _ = run.bootstrap(llm, header, blocks, schema)
        self.assertEqual(len(sug), 3)
        self.assertGreater(len(llm.prompts), 3)

    def test_deployment_without_structured_output(self):
        header, blocks, schema = self.blocks()
        sug, _ = run.bootstrap(FakeAzure(structured=False), header, blocks, schema)
        self.assertEqual(len(sug), 3)

    def test_parse_json_fallbacks(self):
        self.assertEqual(P.parse_json('noise {"items": []} tail'), {"items": []})
        with self.assertRaises(ValueError):
            P.parse_json("no json here")


class PageTests(BootstrapTestBase):
    def run_bootstrap(self, **form):
        with mock.patch.object(azure_settings, "client", lambda: FakeAzure()):
            return self.client.post("/map-fields/bootstrap/", dict(action="run", **form), follow=True)

    def test_requires_azure_settings_and_https(self):
        r = self.run_bootstrap()
        self.assertContains(r, "needs the Azure OpenAI endpoint")
        r = self.run_bootstrap(azure_endpoint="http://evil.example/", azure_deployment="d", azure_api_key="k")
        self.assertContains(r, "must be an https:// URL")
        self.assertFalse(azure_settings.configured())

    def test_suggestions_prefill_page_and_save_through_normal_validation(self):
        r = self.run_bootstrap(azure_endpoint="https://res.openai.azure.com/", azure_deployment="gpt",
                               azure_api_key="KEY-123")
        self.assertContains(r, "proposed a table for 3 of 3 rules")
        page = self.client.get("/map-fields/").content.decode()
        self.assertNotIn("KEY-123", page)
        self.assertIn("shared with Galois mode", page)
        self.assertEqual(page.count('title="Proposed by the LLM'), 3)
        saved = json.loads(re.search(r'id="saved-mappings"[^>]*>(.*?)</script>', page, re.S).group(1))
        self.assertEqual(saved["MAPID-SEX"], {"table": "anagrafica",
                                              "pairs": {"patient": "id_paziente", "sex": "sesso"}})
        self.assertIn('<option value="anagrafica" selected>', page)
        # salvataggio dalla pagina, come farebbe l'amministratore
        post = {}
        for mid, v in saved.items():
            post[f"{mid}__table"] = v["table"]
            post[f"connections_{mid}"] = json.dumps(v["pairs"])
        r = self.client.post("/map-fields/", post)
        self.assertEqual(r.status_code, 302)
        text = self.out.read_text()
        self.assertIn('bto:Patient{id_paziente} a bto:Patient ; bto:sex {sesso}^^xsd:string', text)
        self.assertIn('SELECT id_paziente, eta FROM "esordio"', text)

    def test_saved_bindings_win_over_suggestions(self):
        self.client.post("/map-fields/", {"MAPID-SEX__table": "anagrafica",
                                          "connections_MAPID-SEX": json.dumps({"patient": "id_paziente",
                                                                               "sex": "note"})})
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        self.run_bootstrap()
        page = self.client.get("/map-fields/").content.decode()
        saved = json.loads(re.search(r'id="saved-mappings"[^>]*>(.*?)</script>', page, re.S).group(1))
        self.assertEqual(saved["MAPID-SEX"]["pairs"]["sex"], "note")
        self.assertEqual(page.count('title="Proposed by the LLM'), 2)

    def test_schema_change_invalidates_and_discard(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        self.run_bootstrap()
        con = duckdb.connect(self.db)
        con.execute("ALTER TABLE esordio ADD COLUMN sede VARCHAR")
        con.close()
        self.assertNotIn('title="Proposed by the LLM', self.client.get("/map-fields/").content.decode())
        self.run_bootstrap()
        self.client.post("/map-fields/bootstrap/", {"action": "discard"})
        self.assertNotIn('title="Proposed by the LLM', self.client.get("/map-fields/").content.decode())

    def test_llm_failure_is_reported(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")

        class Broken:
            def chat(self, *a, **k):
                raise LLMError("unauthorized", "HTTP 401: check the API key")
        with mock.patch.object(azure_settings, "client", lambda: Broken()):
            r = self.client.post("/map-fields/bootstrap/", {"action": "run"}, follow=True)
        self.assertContains(r, "Azure OpenAI failed")

    def test_settings_shared_with_galois(self):
        from myapp.galois import store
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        self.assertEqual(store.load_azure()["deployment"], "gpt")

    def test_legacy_galois_settings_migrated(self):
        legacy = Path(self._state.name) / "galois" / "azure.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps({"endpoint": "https://x.openai.azure.com", "deployment": "d",
                                      "api_key": "k"}))
        self.assertTrue(azure_settings.configured())
        self.assertFalse(legacy.exists())
        self.assertTrue(azure_settings.path().exists())
