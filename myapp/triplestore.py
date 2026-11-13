"""GraphDB come sorgente: risponde alle query SPARQL al posto di Ontop.

Un triple store contiene gia' un grafo RDF, quindi non c'e' mapping ne' Ontop.
Il controllo di privacy resta quello di ``protected_sparql``: solo dopo averlo
superato la query ricostruita dal catalogo viene inviata qui.

Le credenziali, se la sicurezza di GraphDB e' attiva, viaggiano come Basic auth:
con credenziali l'URL deve essere HTTPS, salvo un server locale. I messaggi
d'errore non contengono mai la password.
"""
import re
from urllib.parse import quote, urlparse

import requests

from . import datasources as ds

KIND = "graphdb"
TIMEOUT = 10
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class TripleStoreError(Exception):
    """Errore con codice breve, sicuro da registrare; ``detail`` e' per l'amministratore."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def valid_url(url, has_credentials=False):
    """URL http(s) senza credenziali incorporate; con credenziali serve HTTPS (o un server locale)."""
    parts = urlparse((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    return not has_credentials or parts.scheme == "https" or parts.hostname in _LOCAL_HOSTS


def valid_repository(name):
    return bool(_REPOSITORY_RE.match(name or ""))


def _params():
    p = ds.stored(KIND)
    if ds.missing(KIND, None):
        raise TripleStoreError("not-configured", "set the server URL and the repository first")
    return p


def _auth(p):
    return (p["user"], p.get("password", "")) if p.get("user") else None


def _base(p):
    return p["url"].rstrip("/")


def endpoint_url(p=None):
    p = p or _params()
    return f"{_base(p)}/repositories/{quote(p['repository'], safe='')}"


def _scrub(text, p):
    for secret in (p.get("password"),):
        if secret:
            text = text.replace(secret, ds.REDACTED)
    return text


def _fail(exc, p):
    if isinstance(exc, requests.Timeout):
        return TripleStoreError("timeout", "GraphDB did not answer in time")
    return TripleStoreError("unreachable", _scrub(type(exc).__name__, p))


def _check_status(resp, p):
    if resp.status_code in (401, 403):
        raise TripleStoreError("unauthorized", f"HTTP {resp.status_code}: check the user and password")
    if resp.status_code == 404:
        raise TripleStoreError("not-found", "HTTP 404: check the server URL and the repository name")
    if resp.status_code >= 400:
        raise TripleStoreError("http-error", _scrub(f"HTTP {resp.status_code}: {resp.text[:200]}", p))


def query(sparql, timeout=TIMEOUT):
    """Esegue la query sul repository e restituisce il JSON dei risultati SPARQL."""
    p = _params()
    try:
        resp = requests.post(endpoint_url(p), data={"query": sparql}, auth=_auth(p), timeout=timeout,
                             headers={"Accept": "application/sparql-results+json"})
    except requests.RequestException as exc:
        raise _fail(exc, p)
    _check_status(resp, p)
    try:
        return resp.json()
    except ValueError:
        raise TripleStoreError("bad-response", "GraphDB did not return SPARQL JSON results")


def test():
    """Verifica server, credenziali e repository. Restituisce il numero di statement, se noto."""
    p = _params()
    query("ASK { ?s ?p ?o }", timeout=ds.TIMEOUT)
    try:
        resp = requests.get(endpoint_url(p) + "/size", auth=_auth(p), timeout=ds.TIMEOUT)
        if resp.status_code == 200 and resp.text.strip().isdigit():
            return int(resp.text.strip())
    except requests.RequestException:
        pass
    return None
