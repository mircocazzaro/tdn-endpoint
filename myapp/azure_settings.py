"""Azure OpenAI settings of the endpoint, shared by every LLM feature.

Galois mode and the mapping bootstrap use the same endpoint, deployment and
key: the administrator enters them once. Stored in HDN_STATE_DIR/azure.json
with permissions 0600; the key is never shown back. Older installations kept
them in HDN_STATE_DIR/galois/azure.json, which is migrated on first read.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings

from .galois.llm import AzureOpenAI

_LOCAL_HTTP_RE = re.compile(r"http://(127\.0\.0\.1|localhost)[:/]")


def path():
    return Path(settings.HDN_STATE_DIR) / "azure.json"


def _legacy_path():
    return Path(settings.HDN_STATE_DIR) / "galois" / "azure.json"


def _write(data):
    p = path()
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


def load():
    for p in (path(), _legacy_path()):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            continue
        if p != path():
            _write(data)
            p.unlink()
        return data
    return {}


def save(endpoint, deployment, api_version, api_key=None):
    """Save the settings; ``api_key`` None (or empty) keeps the stored one."""
    current = load()
    _write({"endpoint": (endpoint or "").strip(), "deployment": (deployment or "").strip(),
            "api_version": (api_version or "").strip() or None,
            "api_key": api_key.strip() if api_key and api_key.strip() else current.get("api_key", "")})


def configured():
    a = load()
    return bool(a.get("endpoint") and a.get("deployment") and a.get("api_key"))


def valid_endpoint(url):
    """The key travels in a header: HTTPS only, except a local proxy."""
    return bool(url) and (url.startswith("https://") or bool(_LOCAL_HTTP_RE.match(url)))


def public():
    """Settings without the key, for the pages."""
    a = load()
    return {"endpoint": a.get("endpoint", ""), "deployment": a.get("deployment", ""),
            "api_version": a.get("api_version") or "", "has_key": bool(a.get("api_key"))}


def client():
    """AzureOpenAI client; galois.llm.LLMError('not-configured') if settings are missing."""
    a = load()
    return AzureOpenAI(a.get("endpoint"), a.get("deployment"), a.get("api_key"), a.get("api_version"))
