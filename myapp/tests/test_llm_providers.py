"""Provider LLM (Settings > Endpoint configuration) e adapter Bedrock, Vertex AI, watsonx.

Le risposte dei servizi sono simulate: il formato delle richieste segue la
documentazione dei provider, ma non e' verificato contro servizi reali.
"""
import json
import os
import stat
from unittest import mock

from django.test import Client, TestCase

from myapp import azure_settings, llm_adapters, llm_providers
from myapp.galois.llm import LLMError
from myapp.llm_providers import BEDROCK, FOUNDRY, VERTEX, WATSONX

from .hdn_helpers import StateDirMixin


def _resp(status=200, payload=None, text=""):
    r = mock.Mock(status_code=status, text=text or json.dumps(payload or {}))
    r.json.return_value = payload or {}
    return r


class ResolveTests(StateDirMixin, TestCase):

    def test_none_configured(self):
        with self.assertRaises(LLMError) as cm:
            llm_providers.resolve()
        self.assertEqual(cm.exception.code, "not-configured")

    def test_single_provider_is_used_without_asking(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        self.assertEqual(llm_providers.resolve(), FOUNDRY)

    def test_several_providers_require_a_choice(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        llm_providers.save(WATSONX, {"url": "https://eu-de.ml.cloud.ibm.com", "project_id": "p",
                                     "model_id": "m", "api_key": "k"})
        with self.assertRaises(LLMError) as cm:
            llm_providers.resolve()
        self.assertEqual(cm.exception.code, "provider-required")
        self.assertEqual(llm_providers.resolve(WATSONX), WATSONX)
        self.assertEqual([c["id"] for c in llm_providers.choices()], [FOUNDRY, WATSONX])

    def test_requested_provider_must_be_available(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        with self.assertRaises(LLMError):
            llm_providers.resolve(BEDROCK)


class StorageTests(StateDirMixin, TestCase):

    def test_blank_secret_keeps_the_stored_one_and_file_is_private(self):
        llm_providers.save(BEDROCK, {"region": "eu-central-1", "model_id": "m",
                                     "access_key_id": "AKIA", "secret_access_key": "S3CRET"})
        llm_providers.save(BEDROCK, {"region": "eu-west-1", "model_id": "m2",
                                     "access_key_id": "AKIA", "secret_access_key": ""})
        stored = llm_providers.load(BEDROCK)
        self.assertEqual((stored["region"], stored["secret_access_key"]), ("eu-west-1", "S3CRET"))
        self.assertEqual(stat.S_IMODE(os.stat(llm_providers._path()).st_mode), 0o600)

    def test_public_view_never_contains_secrets(self):
        llm_providers.save(VERTEX, {"project_id": "p", "location": "europe-west4", "model": "g",
                                    "service_account": '{"private_key": "PRIVATE"}'})
        pub = llm_providers.public(VERTEX)
        self.assertEqual(pub["project_id"], "p")
        self.assertTrue(pub["has_service_account"])
        self.assertNotIn("PRIVATE", json.dumps(pub))

    def test_missing_required_fields_are_listed(self):
        self.assertEqual(llm_providers.missing(WATSONX, {"url": "https://x"}),
                         ["Project ID", "Model ID", "IBM Cloud API key"])
        self.assertFalse(llm_providers.configured(WATSONX))


class BedrockAdapterTests(TestCase):

    def test_converse_gets_system_separately_and_returns_text(self):
        runtime = mock.Mock()
        runtime.converse.return_value = {"output": {"message": {"content": [{"text": "OK"}]}},
                                         "usage": {"totalTokens": 7}, "stopReason": "end_turn"}
        c = llm_adapters.BedrockClient("eu-central-1", "anthropic.model", "AKIA", "S", runtime=runtime)
        r = c.chat([{"role": "system", "content": "be exact"}, {"role": "user", "content": "hi"}],
                   max_tokens=50)
        body = runtime.converse.call_args.kwargs
        self.assertEqual(body["system"], [{"text": "be exact"}])
        self.assertEqual(body["messages"], [{"role": "user", "content": [{"text": "hi"}]}])
        self.assertEqual(body["inferenceConfig"]["maxTokens"], 50)
        self.assertEqual((r.text, r.usage_tokens, r.finish_reason), ("OK", 7, "end_turn"))

    def test_access_denied_is_unauthorized_without_secrets(self):
        class ClientError(Exception):
            response = {"Error": {"Code": "AccessDeniedException"}}
        runtime = mock.Mock()
        runtime.converse.side_effect = ClientError("denied for key AKIA-SECRET")
        c = llm_adapters.BedrockClient("eu-central-1", "m", "AKIA", "S", runtime=runtime)
        with self.assertRaises(LLMError) as cm:
            c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(cm.exception.code, "unauthorized")
        self.assertNotIn("AKIA", str(cm.exception))


class VertexAdapterTests(TestCase):

    def _creds(self):
        creds = mock.Mock(valid=True, token="ACCESS-TOKEN")
        return creds

    def test_generate_content_on_the_regional_endpoint(self):
        http = mock.Mock()
        http.post.return_value = _resp(payload={
            "candidates": [{"content": {"parts": [{"text": "Ciao"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"totalTokenCount": 12}})
        c = llm_adapters.VertexClient("proj", "europe-west4", "gemini-2.0-flash",
                                      '{"type": "service_account"}', session=http,
                                      credentials=self._creds())
        r = c.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}])
        url = http.post.call_args.args[0]
        self.assertEqual(url, "https://europe-west4-aiplatform.googleapis.com/v1/projects/proj/"
                              "locations/europe-west4/publishers/google/models/gemini-2.0-flash:generateContent")
        self.assertEqual(http.post.call_args.kwargs["headers"]["Authorization"], "Bearer ACCESS-TOKEN")
        body = http.post.call_args.kwargs["json"]
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "hi"}]}])
        self.assertEqual((r.text, r.usage_tokens, r.finish_reason), ("Ciao", 12, "STOP"))

    def test_forbidden_is_unauthorized(self):
        http = mock.Mock()
        http.post.return_value = _resp(status=403, text="permission denied")
        c = llm_adapters.VertexClient("p", "l", "m", '{"a": 1}', session=http,
                                      credentials=self._creds())
        with self.assertRaises(LLMError) as cm:
            c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(cm.exception.code, "unauthorized")

    def test_invalid_service_account_json_is_rejected_at_construction(self):
        with self.assertRaises(LLMError) as cm:
            llm_adapters.VertexClient("p", "l", "m", "not json")
        self.assertEqual(cm.exception.code, "not-configured")


class WatsonxAdapterTests(TestCase):

    def _http(self, chat_payload):
        http = mock.Mock()
        iam = _resp(payload={"access_token": "IAM-TOKEN", "expires_in": 3600})
        http.post.side_effect = [iam, _resp(payload=chat_payload), _resp(payload=chat_payload)]
        return http

    CHAT = {"choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 5}}

    def test_iam_token_then_chat_and_token_is_cached(self):
        http = self._http(self.CHAT)
        c = llm_adapters.WatsonxClient("https://eu-de.ml.cloud.ibm.com/", "proj", "ibm/granite",
                                       "APIKEY", session=http)
        r1 = c.chat([{"role": "user", "content": "hi"}])
        c.chat([{"role": "user", "content": "again"}])
        iam_calls = [x for x in http.post.call_args_list if x.args[0] == llm_adapters.IBM_IAM_URL]
        self.assertEqual(len(iam_calls), 1)
        chat_call = http.post.call_args_list[1]
        self.assertEqual(chat_call.args[0], "https://eu-de.ml.cloud.ibm.com/ml/v1/text/chat")
        self.assertEqual(chat_call.kwargs["headers"]["Authorization"], "Bearer IAM-TOKEN")
        self.assertEqual(chat_call.kwargs["json"]["project_id"], "proj")
        self.assertEqual((r1.text, r1.usage_tokens), ("Hello", 5))

    def test_bad_api_key_fails_at_iam(self):
        http = mock.Mock()
        http.post.return_value = _resp(status=400, text="invalid apikey")
        c = llm_adapters.WatsonxClient("https://x", "p", "m", "bad", session=http)
        with self.assertRaises(LLMError) as cm:
            c.chat([{"role": "user", "content": "x"}])
        self.assertEqual(cm.exception.code, "unauthorized")
        self.assertNotIn("bad", str(cm.exception))


class SettingsPageTests(StateDirMixin, TestCase):

    def test_page_lists_the_four_providers_without_secrets(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "TOPSECRET")
        page = Client().get("/settings/").content.decode()
        for name in ("Microsoft Foundry", "Amazon Bedrock", "Google Cloud (Vertex AI)", "IBM watsonx.ai"):
            self.assertIn(name, page)
        self.assertNotIn("TOPSECRET", page)
        self.assertIn("stored: leave empty to keep it", page)

    def test_test_button_disabled_when_unconfigured(self):
        r = Client().get("/settings/")
        self.assertIn('name="action" value="test" class="btn btn-outline-secondary btn-sm" disabled',
                      r.content.decode())

    def test_missing_fields_are_reported_and_nothing_is_saved(self):
        r = Client().post("/settings/", {"provider": BEDROCK, "action": "save", "region": "eu-central-1"},
                          follow=True)
        self.assertContains(r, "missing")
        self.assertFalse(llm_providers.configured(BEDROCK))

    def test_test_action_reports_adapter_errors(self):
        class Broken:
            def chat(self, *a, **k):
                raise LLMError("unauthorized", "HTTP 401: check the credentials")
        llm_providers.save(BEDROCK, {"region": "r", "model_id": "m", "access_key_id": "a",
                                     "secret_access_key": "s"})
        with mock.patch.object(llm_providers, "client", lambda pid: Broken()):
            r = Client().post("/settings/", {"provider": BEDROCK, "action": "test"}, follow=True)
        self.assertContains(r, "Amazon Bedrock test failed")


class ProviderChoicePageTests(StateDirMixin, TestCase):

    def test_one_provider_gives_a_direct_button_without_modal(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        page = Client().get("/map-fields/").content.decode()
        self.assertIn('name="action" value="run"', page)
        self.assertNotIn('id="llmPickBootstrap"', page)

    def test_several_providers_ask_which_one_to_use(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        llm_providers.save(WATSONX, {"url": "https://x.example", "project_id": "p",
                                     "model_id": "m", "api_key": "k"})
        page = Client().get("/map-fields/").content.decode()
        self.assertIn('id="llmPickBootstrap"', page)
        self.assertIn('data-bs-target="#llmPickBootstrap" data-llm-action="run"', page)
        self.assertIn('value="watsonx"', page)

    def test_bootstrapped_button_is_disabled_until_a_change(self):
        from myapp.bootstrap import run
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        with mock.patch.object(run, "load", lambda fp: {"suggestions": {}, "summary": {
                "suggested": 0, "rules": 0}}):
            page = Client().get("/map-fields/").content.decode()
        self.assertIn('id="bootstrap-run"', page)
        self.assertIn("bootstrap-done", page)
        self.assertRegex(page, r'id="bootstrap-run"[^>]*\bdisabled\b')


class GaloisProviderChoiceTests(StateDirMixin, TestCase):

    def test_galois_asks_for_the_provider_when_several_are_available(self):
        azure_settings.save("https://res.openai.azure.com/", "gpt", "", "k")
        llm_providers.save(BEDROCK, {"region": "r", "model_id": "m", "access_key_id": "a",
                                     "secret_access_key": "s"})
        from myapp.galois import store
        cfg = store.load_config()
        cfg["enabled"] = True
        store.save_config(cfg)
        page = Client().get("/galois/").content.decode()
        self.assertIn('id="llmPicker"', page)
        self.assertIn('name="llm_default"', page)
        self.assertIn('data-llm-action="refresh:', page)


class FoundryAnthropicTests(StateDirMixin, TestCase):
    URL = "https://codingfoundrygms.services.ai.azure.com/anthropic/v1/messages"

    def test_messages_request_with_x_api_key_and_no_api_version(self):
        http = mock.Mock()
        http.post.return_value = _resp(payload={
            "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 2}})
        c = llm_adapters.AnthropicFoundry(self.URL, "claude-haiku-5-5-mirco", "KEY", session=http)
        r = c.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
                   max_tokens=20)
        self.assertEqual(http.post.call_args.args[0], self.URL)
        headers = http.post.call_args.kwargs["headers"]
        self.assertEqual(headers["x-api-key"], "KEY")
        self.assertEqual(headers["anthropic-version"], llm_adapters.ANTHROPIC_VERSION)
        body = http.post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "claude-haiku-5-5-mirco")
        self.assertEqual(body["system"], "sys")
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertNotIn("temperature", body)  # deprecato per i modelli Claude recenti
        self.assertEqual((r.text, r.usage_tokens, r.finish_reason), ("OK", 5, "end_turn"))

    def test_url_without_messages_suffix_is_completed(self):
        c = llm_adapters.AnthropicFoundry(self.URL.replace("/v1/messages", ""), "m", "k")
        self.assertEqual(c.url, self.URL)

    def test_client_factory_routes_by_url(self):
        from myapp.galois.llm import AzureOpenAI
        azure_settings.save(self.URL.replace("/v1/messages", ""), "claude-haiku-5-5-mirco", "", "k")
        self.assertIsInstance(azure_settings.client(), llm_adapters.AnthropicFoundry)
        azure_settings.save("https://res.openai.azure.com/", "gpt", "2024-10-21", "k")
        self.assertIsInstance(azure_settings.client(), AzureOpenAI)

    def test_anthropic_path_detection(self):
        self.assertTrue(llm_adapters.is_anthropic_url(self.URL))
        self.assertTrue(llm_adapters.is_anthropic_url("https://x.example/anthropic"))
        self.assertFalse(llm_adapters.is_anthropic_url("https://x.openai.azure.com/anthropics"))
        self.assertFalse(llm_adapters.is_anthropic_url("https://res.openai.azure.com/openai/v1"))
