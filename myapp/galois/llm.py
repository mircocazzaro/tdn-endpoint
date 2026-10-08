"""Client Azure OpenAI (chat completions) per Galois.

Due forme di URL, come le mostra il portale Azure:
- risorsa Azure OpenAI, es. ``https://nome.openai.azure.com/``: chiamata a
  ``/openai/deployments/{deployment}/chat/completions?api-version=...``;
- API v1 (Azure AI Foundry), URL che termina con ``/openai/v1``: chiamata a
  ``/chat/completions`` con ``model = deployment``.

La chiave va solo nell'header ``api-key`` e non compare mai nei messaggi di
errore. Decodifica deterministica (temperatura 0), come in py-galois.
"""
import re
import time

import requests

DEFAULT_API_VERSION = "2024-10-21"
TIMEOUT = 90
MAX_TOKENS = 4000
_V1_RE = re.compile(r"/openai/v1/?$")


class LLMError(Exception):
    """Errore nella chiamata all'LLM. ``code`` e' breve e sicuro da registrare."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class LLMResponse:
    def __init__(self, text, usage_tokens=0, latency_s=0.0, finish_reason=None):
        self.text = text
        self.usage_tokens = usage_tokens
        self.latency_s = latency_s
        self.finish_reason = finish_reason


class AzureOpenAI:
    def __init__(self, endpoint, deployment, api_key, api_version=DEFAULT_API_VERSION,
                 session=None):
        if not (endpoint and deployment and api_key):
            raise LLMError("not-configured", "endpoint, deployment and API key are required")
        self.endpoint = endpoint.rstrip("/")
        self.deployment = deployment
        self._key = api_key
        self.api_version = api_version or DEFAULT_API_VERSION
        self._http = session or requests

    def _request(self, messages, max_tokens=MAX_TOKENS, response_format=None):
        body = {"messages": messages, "temperature": 0, "top_p": 1, "max_tokens": max_tokens}
        if response_format is not None:
            body["response_format"] = response_format
        if _V1_RE.search(self.endpoint + "/"):
            url, params = self.endpoint + "/chat/completions", None
            body["model"] = self.deployment
        else:
            url = f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions"
            params = {"api-version": self.api_version}
        return self._http.post(url, params=params, json=body, timeout=TIMEOUT,
                               headers={"api-key": self._key, "Content-Type": "application/json"})

    def chat(self, messages, **kwargs):
        t0 = time.time()
        for attempt in range(3):
            try:
                resp = self._request(messages, kwargs.get("max_tokens", MAX_TOKENS),
                                     kwargs.get("response_format"))
            except requests.RequestException as exc:
                raise LLMError("unreachable", type(exc).__name__)
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                try:
                    wait = min(float(resp.headers.get("Retry-After", 2)), 20)
                except ValueError:
                    wait = 2
                time.sleep(wait)
                continue
            break
        if resp.status_code in (401, 403):
            raise LLMError("unauthorized", f"HTTP {resp.status_code}: check the API key")
        if resp.status_code == 404:
            raise LLMError("not-found", "HTTP 404: check the endpoint URL and the deployment name")
        if resp.status_code >= 400:
            raise LLMError("http-error", f"HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"].get("content") or ""
            finish = data["choices"][0].get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("bad-response", "unexpected response format")
        usage = data.get("usage") or {}
        tokens = usage.get("total_tokens") or (
            (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0))
        return LLMResponse(text, tokens, time.time() - t0, finish)
