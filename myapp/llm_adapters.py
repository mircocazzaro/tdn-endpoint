"""Adapter LLM per Amazon Bedrock, Google Cloud (Vertex AI) e IBM watsonx.ai.

Ogni adapter espone ``chat(messages, **kwargs) -> LLMResponse``, come
``galois.llm.AzureOpenAI``, e segnala gli errori con ``LLMError`` e un codice
breve. Le credenziali non compaiono mai nei messaggi di errore.

I formati delle richieste seguono la documentazione dei provider; non sono
verificati contro servizi reali in questo ambiente (test con risposte simulate).
"""
import time
from urllib.parse import urlparse

import requests

from .galois.llm import MAX_TOKENS, TIMEOUT, LLMError, LLMResponse

IBM_IAM_URL = "https://iam.cloud.ibm.com/identity/token"
WATSONX_API_VERSION = "2024-05-15"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _split_system(messages):
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [m for m in messages if m["role"] != "system"]
    return system, rest


def _http_error(status, text_hint=""):
    if status in (401, 403):
        return LLMError("unauthorized", f"HTTP {status}: check the credentials")
    if status == 404:
        return LLMError("not-found", "HTTP 404: check the URL, the project and the model")
    return LLMError("http-error", f"HTTP {status}: {text_hint[:200]}")


ANTHROPIC_VERSION = "2023-06-01"


def is_anthropic_url(url):
    """Endpoint Foundry che parla il protocollo Messages di Anthropic (.../anthropic)."""
    if not url:
        return False
    path = urlparse(url).path.rstrip("/")
    return path == "/anthropic" or path.startswith("/anthropic/")


class AnthropicFoundry:
    """Microsoft Foundry, modelli Claude: POST {base}/v1/messages con header x-api-key.

    Non serve una API version: l'header anthropic-version e' fisso. Accetta l'URL
    con o senza il suffisso /v1/messages.
    """

    def __init__(self, endpoint, deployment, api_key, session=None):
        if not (endpoint and deployment and api_key):
            raise LLMError("not-configured", "endpoint, model and API key are required")
        base = endpoint.rstrip("/")
        self.url = base if base.endswith("/v1/messages") else base + "/v1/messages"
        self.model = deployment
        self._key = api_key
        self._http = session or requests

    def chat(self, messages, **kwargs):
        t0 = time.time()
        system, rest = _split_system(messages)
        body = {"model": self.model,
                "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
                # niente temperature/top_p: sono deprecati per i modelli Claude recenti
                "messages": [{"role": m["role"], "content": m["content"]} for m in rest]}
        if system:
            body["system"] = system
        try:
            resp = self._http.post(self.url, json=body, timeout=TIMEOUT, headers={
                "x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"})
        except requests.RequestException as exc:
            raise LLMError("unreachable", type(exc).__name__)
        if resp.status_code >= 400:
            raise _http_error(resp.status_code, resp.text)
        try:
            data = resp.json()
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage") or {}
            tokens = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            stop = data.get("stop_reason")
        except (ValueError, KeyError, TypeError):
            raise LLMError("bad-response", "unexpected response format")
        return LLMResponse(text, tokens, time.time() - t0, stop)


class BedrockClient:
    """Amazon Bedrock tramite Converse API (boto3, firma SigV4 gestita dalla libreria)."""

    def __init__(self, region, model_id, access_key_id, secret_access_key, runtime=None):
        if not (region and model_id and access_key_id and secret_access_key):
            raise LLMError("not-configured", "region, model ID and AWS keys are required")
        self.region = region
        self.model_id = model_id
        self._keys = (access_key_id, secret_access_key)
        self._runtime = runtime

    def _client(self):
        if self._runtime is None:
            import boto3
            self._runtime = boto3.client(
                "bedrock-runtime", region_name=self.region,
                aws_access_key_id=self._keys[0], aws_secret_access_key=self._keys[1])
        return self._runtime

    def chat(self, messages, **kwargs):
        t0 = time.time()
        system, rest = _split_system(messages)
        body = {
            "modelId": self.model_id,
            "messages": [{"role": m["role"], "content": [{"text": m["content"]}]} for m in rest],
            "inferenceConfig": {"maxTokens": kwargs.get("max_tokens", MAX_TOKENS),
                                "temperature": 0, "topP": 1},
        }
        if system:
            body["system"] = [{"text": system}]
        try:
            resp = self._client().converse(**body)
        except Exception as exc:  # botocore: ClientError, EndpointConnectionError, ...
            response = getattr(exc, "response", None) or {}
            code = (response.get("Error") or {}).get("Code", "")
            if code in ("AccessDeniedException", "UnrecognizedClientException",
                        "ExpiredTokenException", "InvalidSignatureException"):
                raise LLMError("unauthorized", code)
            if code == "ResourceNotFoundException":
                raise LLMError("not-found", "check the model ID and its access in Bedrock")
            if response:
                raise LLMError("http-error", code or type(exc).__name__)
            raise LLMError("unreachable", type(exc).__name__)
        try:
            text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
        except (KeyError, TypeError):
            raise LLMError("bad-response", "unexpected response format")
        usage = resp.get("usage") or {}
        tokens = usage.get("totalTokens") or (usage.get("inputTokens", 0)
                                              + usage.get("outputTokens", 0))
        return LLMResponse(text, tokens, time.time() - t0, resp.get("stopReason"))


class VertexClient:
    """Google Cloud Vertex AI, modello Gemini (generateContent) con account di servizio."""

    def __init__(self, project_id, location, model, service_account_json,
                 session=None, credentials=None):
        import json
        if not (project_id and location and model and service_account_json):
            raise LLMError("not-configured", "project, location, model and service account are required")
        try:
            self._info = json.loads(service_account_json)
        except ValueError:
            raise LLMError("not-configured", "the service account is not valid JSON")
        self._creds = credentials
        self._http = session or requests
        self.url = (f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}"
                    f"/locations/{location}/publishers/google/models/{model}:generateContent")

    def _token(self):
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
        if self._creds is None:
            self._creds = service_account.Credentials.from_service_account_info(
                self._info, scopes=[GOOGLE_SCOPE])
        if not self._creds.valid:
            self._creds.refresh(Request())
        return self._creds.token

    def chat(self, messages, **kwargs):
        t0 = time.time()
        system, rest = _split_system(messages)
        body = {
            "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                          "parts": [{"text": m["content"]}]} for m in rest],
            "generationConfig": {"temperature": 0, "topP": 1,
                                 "maxOutputTokens": kwargs.get("max_tokens", MAX_TOKENS)},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            token = self._token()
        except Exception as exc:
            raise LLMError("unauthorized",
                           f"no Google access token ({type(exc).__name__}): check the service account")
        try:
            resp = self._http.post(self.url, json=body, timeout=TIMEOUT,
                                   headers={"Authorization": f"Bearer {token}",
                                            "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise LLMError("unreachable", type(exc).__name__)
        if resp.status_code >= 400:
            raise _http_error(resp.status_code, resp.text)
        try:
            data = resp.json()
            candidate = data["candidates"][0]
            text = "".join(p.get("text", "") for p in candidate["content"]["parts"])
            finish = candidate.get("finishReason")
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("bad-response", "unexpected response format")
        tokens = (data.get("usageMetadata") or {}).get("totalTokenCount", 0)
        return LLMResponse(text, tokens, time.time() - t0, finish)


class WatsonxClient:
    """IBM watsonx.ai, chat (/ml/v1/text/chat); token IAM ottenuto dalla chiave API."""

    def __init__(self, url, project_id, model_id, api_key, session=None):
        if not (url and project_id and model_id and api_key):
            raise LLMError("not-configured", "service URL, project ID, model ID and API key are required")
        self.url = url.rstrip("/")
        self.project_id = project_id
        self.model_id = model_id
        self._key = api_key
        self._http = session or requests
        self._token_value = None
        self._token_expiry = 0.0

    def _token(self):
        if self._token_value and time.time() < self._token_expiry - 60:
            return self._token_value
        try:
            resp = self._http.post(
                IBM_IAM_URL, timeout=TIMEOUT,
                data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": self._key},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
        except requests.RequestException as exc:
            raise LLMError("unreachable", type(exc).__name__)
        if resp.status_code >= 400:
            raise LLMError("unauthorized", f"IBM Cloud IAM HTTP {resp.status_code}: check the API key")
        try:
            data = resp.json()
            self._token_value = data["access_token"]
            self._token_expiry = time.time() + float(data.get("expires_in", 3000))
        except (ValueError, KeyError, TypeError):
            raise LLMError("bad-response", "unexpected IAM response")
        return self._token_value

    def chat(self, messages, **kwargs):
        t0 = time.time()
        body = {
            "model_id": self.model_id,
            "project_id": self.project_id,
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", MAX_TOKENS),
            "temperature": 0,
            "top_p": 1,
        }
        token = self._token()
        try:
            resp = self._http.post(
                f"{self.url}/ml/v1/text/chat", params={"version": WATSONX_API_VERSION},
                json=body, timeout=TIMEOUT,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise LLMError("unreachable", type(exc).__name__)
        if resp.status_code >= 400:
            raise _http_error(resp.status_code, resp.text)
        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            finish = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("bad-response", "unexpected response format")
        tokens = (data.get("usage") or {}).get("total_tokens", 0)
        return LLMResponse(text, tokens, time.time() - t0, finish)
