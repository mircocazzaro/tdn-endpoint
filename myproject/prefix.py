"""Prefisso di sotto-percorso dell'endpoint, riconosciuto a ogni richiesta, senza configurazione.

Il DNS dell'istituto puo' pubblicare il servizio sotto un percorso qualsiasi
(tdn.dei.unipd.it/api1, hdn-endpoint.com/abcd, ...) e il proxy puo' togliere o
lasciare quel percorso. Il prefisso si ricava cosi':

1. se il proxy invia X-Forwarded-Prefix, si usa quel valore;
2. se il percorso cosi' com'e' corrisponde a una pagina, non c'e' prefisso;
3. altrimenti, se togliendo il primo segmento il resto corrisponde a una pagina
   (o a un file statico), il primo segmento e' il prefisso;
4. in tutti gli altri casi non c'e' prefisso (la risposta sara' 404 come sempre).

Le pagine sono quelle del routing di Django: nessuna lista da mantenere. Il
prefisso trovato entra in SCRIPT_NAME, cosi' reverse(), redirect() e i link
lo includono, e in una variabile locale al thread letta dalla storage degli
statici (myapp/storage.py).

Una richiesta senza prefisso, per esempio l'health check del container su
127.0.0.1:8000, passa invariata.
"""
import threading

from django.urls import Resolver404, resolve

_local = threading.local()


def current_prefix():
    """Prefisso della richiesta in corso, o stringa vuota."""
    return getattr(_local, "prefix", "")


def _resolves(path):
    if path.startswith("/static/"):
        return True
    try:
        resolve(path)
    except Resolver404:
        return False
    return True


def _valid_header(value):
    return value.startswith("/") and ".." not in value and "//" not in value and not value.endswith("/")


def detect(environ):
    """Prefisso della richiesta (es. '/api1'), o '' se non ce n'e'."""
    header = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip().rstrip("/")
    if header and _valid_header(header):
        return header
    path = environ.get("PATH_INFO", "") or "/"
    if _resolves(path):
        return ""
    # i primi k segmenti (k crescente) sono il prefisso se il resto corrisponde a una pagina
    segments = [seg for seg in path.split("/")[1:]]
    for k in range(1, len(segments) + 1):
        prefix = "/" + "/".join(segments[:k])
        rest = path[len(prefix):] or "/"
        if _resolves(rest):
            return prefix
    return ""


class PrefixApp:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = detect(environ)
        if prefix:
            path = environ.get("PATH_INFO", "")
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + prefix
            # il proxy puo' aver gia' tolto il prefisso (header X-Forwarded-Prefix): in quel caso
            # il percorso resta com'e'
            if path == prefix or path.startswith(prefix + "/"):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        _local.prefix = prefix
        try:
            return self.app(environ, start_response)
        finally:
            _local.prefix = ""
