"""Provider LLM configurati dall'amministratore (Settings > Endpoint configuration).

Microsoft Foundry usa le impostazioni Azure gia' esistenti (myapp/azure_settings.py,
stesso file, nessuna migrazione). Gli altri provider stanno in
HDN_STATE_DIR/llm_providers.json con permessi 0600. Come per Azure, i segreti
non tornano mai alle pagine: si mostra solo se sono presenti.

Un provider e' "disponibile" quando tutti i suoi campi obbligatori sono
impostati. I task che usano un LLM (bootstrap dei mapping, Galois) chiedono
quale usare solo se i provider disponibili sono piu' di uno.
"""
import json
import os
import tempfile
from collections import namedtuple
from pathlib import Path

from django.conf import settings

from . import azure_settings
from .galois.llm import LLMError

FOUNDRY, BEDROCK, VERTEX, WATSONX = "foundry", "bedrock", "vertex", "watsonx"

Field = namedtuple("Field", "name label secret required placeholder textarea", defaults=(False, True, "", False))

PROVIDERS = {
    FOUNDRY: ("Microsoft Foundry", [
        Field("endpoint", "Endpoint URL", placeholder="https://<resource>.openai.azure.com/, .../openai/v1, or .../anthropic (Claude)"),
        Field("deployment", "Deployment", placeholder="gpt-4o-mini"),
        Field("api_version", "API version", placeholder="2024-10-21 (OpenAI endpoints only)", required=False),
        Field("api_key", "API key", secret=True),
    ]),
    BEDROCK: ("Amazon Bedrock", [
        Field("region", "AWS region", placeholder="eu-central-1"),
        Field("model_id", "Model ID", placeholder="anthropic.claude-3-5-sonnet-20240620-v1:0"),
        Field("access_key_id", "Access key ID"),
        Field("secret_access_key", "Secret access key", secret=True),
    ]),
    VERTEX: ("Google Cloud (Vertex AI)", [
        Field("project_id", "Project ID"),
        Field("location", "Location", placeholder="europe-west4"),
        Field("model", "Model", placeholder="gemini-2.0-flash"),
        Field("service_account", "Service account JSON", secret=True, textarea=True),
    ]),
    WATSONX: ("IBM watsonx.ai", [
        Field("url", "Service URL", placeholder="https://eu-de.ml.cloud.ibm.com"),
        Field("project_id", "Project ID"),
        Field("model_id", "Model ID", placeholder="ibm/granite-3-8b-instruct"),
        Field("api_key", "IBM Cloud API key", secret=True),
    ]),
}

URL_FIELDS = {FOUNDRY: "endpoint", WATSONX: "url"}
MODEL_FIELD = {FOUNDRY: "deployment", BEDROCK: "model_id", VERTEX: "model", WATSONX: "model_id"}


def label(pid):
    return PROVIDERS[pid][0]


def fields(pid):
    return PROVIDERS[pid][1]


def _path():
    return Path(settings.HDN_STATE_DIR) / "llm_providers.json"


def _load_others():
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _write_others(data):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(pid):
    """Valori salvati, segreti inclusi: uso interno, mai nelle pagine."""
    if pid == FOUNDRY:
        return azure_settings.load()
    return _load_others().get(pid, {})


def missing(pid, values):
    """Campi obbligatori assenti, considerando i valori nuovi e quelli salvati."""
    stored = load(pid)
    out = []
    for f in fields(pid):
        if not f.required:
            continue
        if values is None:
            value = stored.get(f.name, "")
        else:
            value = (values.get(f.name) or "").strip()
            if not value and f.secret:
                value = stored.get(f.name, "")
        if not (value or "").strip():
            out.append(f.label)
    return out


def save(pid, values):
    """Salva i valori del provider; un segreto lasciato vuoto conserva quello attuale."""
    if pid == FOUNDRY:
        azure_settings.save(values.get("endpoint", ""), values.get("deployment", ""),
                            values.get("api_version", ""), values.get("api_key") or None)
        return
    stored = load(pid)
    new = {}
    for f in fields(pid):
        value = (values.get(f.name) or "").strip()
        if not value and f.secret:
            value = stored.get(f.name, "")
        new[f.name] = value
    data = _load_others()
    data[pid] = new
    _write_others(data)


def configured(pid):
    return not missing(pid, None)


def configured_ids():
    return [pid for pid in PROVIDERS if configured(pid)]


def public(pid):
    """Valori per le pagine: i segreti diventano il flag has_<campo>."""
    stored = load(pid)
    out = {}
    for f in fields(pid):
        if f.secret:
            out[f"has_{f.name}"] = bool(stored.get(f.name))
        else:
            out[f.name] = stored.get(f.name, "") or ""
    return out


def model_name(pid):
    return load(pid).get(MODEL_FIELD[pid], "") if pid in MODEL_FIELD else ""


def choices():
    """Provider disponibili, per i menu di scelta nei task che usano un LLM."""
    return [{"id": pid, "label": label(pid), "model": model_name(pid)}
            for pid in configured_ids()]


def resolve(requested=""):
    """Provider da usare: quello richiesto, oppure l'unico disponibile."""
    ids = configured_ids()
    if requested:
        if requested in ids:
            return requested
        raise LLMError("not-configured", "the selected provider is not configured")
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise LLMError("not-configured",
                       "no LLM provider is configured: see Settings > Endpoint configuration")
    raise LLMError("provider-required", "more than one LLM provider is configured: choose one")


def client(pid):
    """Client del provider; LLMError('not-configured') se mancano campi."""
    if not configured(pid):
        raise LLMError("not-configured", f"{label(pid)} is not configured")
    v = load(pid)
    if pid == FOUNDRY:
        return azure_settings.client()
    from .llm_adapters import BedrockClient, VertexClient, WatsonxClient
    if pid == BEDROCK:
        return BedrockClient(v["region"], v["model_id"], v["access_key_id"], v["secret_access_key"])
    if pid == VERTEX:
        return VertexClient(v["project_id"], v["location"], v["model"], v["service_account"])
    if pid == WATSONX:
        return WatsonxClient(v["url"], v["project_id"], v["model_id"], v["api_key"])
    raise LLMError("not-configured", f"unknown provider {pid}")
