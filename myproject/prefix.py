"""Pubblicazione sotto un sotto-percorso del dominio (tdn.dei.unipd.it/api1), senza configurazione.

Il DNS puo' essere eterogeneo e non e' sotto il controllo di chi installa
l'endpoint. Si incontrano due comportamenti del proxy.

A) Il proxy NON toglie il prefisso: il percorso arriva come /api1/settings/.
   Lo riconosce PrefixApp (detect): il prefisso va in SCRIPT_NAME e reverse(),
   i link e i redirect lo includono, qualunque sia il prefisso.

B) Il proxy LO TOGLIE (Apache ``ProxyPass /api1 http://127.0.0.1:8071/``): arriva
   /settings/ e nessun header porta il prefisso. L'applicazione non puo'
   conoscerlo, ma puo' non averne bisogno:

   - i redirect sono assoluti verso l'indirizzo del backend (l'Host che il proxy
     ha inoltrato, es. 127.0.0.1:8071): ``ProxyPassReverse`` li riconosce e li
     riscrive con il prefisso. Vedi myapp/proxy.py;
   - gli URL nelle pagine (link, form, statici, fetch) sono relativi alla pagina:
     reverse() usa come prefisso "../" ripetuto per la profondita' del percorso,
     e il browser li risolve sotto qualunque prefisso.

Senza proxy (nessun X-Forwarded-Host) tutto resta com'era: URL dalla radice.
"""
import os
import re
import threading

from django.urls import Resolver404, resolve

_local = threading.local()


def current_prefix():
    """Prefisso assoluto riconosciuto nel percorso della richiesta in corso, o stringa vuota."""
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


# Convenzione dell'istituto: sotto questi domini ogni istanza e' pubblicata come /api<x>, dove <x> e'
# l'ultima cifra della porta a cui il proxy la inoltra (127.0.0.1:8071 -> /api1). Il proxy mette il
# dominio in X-Forwarded-Host e la sua porta nell'Host che inoltra. HDN_PORT_PREFIX_HOSTS (elenco
# separato da virgole) cambia i domini; vuoto disattiva la regola.
DEFAULT_PORT_PREFIX_HOSTS = "tdn.dei.unipd.it"


def _port_prefix_hosts():
    raw = os.environ.get("HDN_PORT_PREFIX_HOSTS", DEFAULT_PORT_PREFIX_HOSTS)
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def port_rule_prefix(environ):
    """'/api<x>' per un dominio della convenzione, ricavando x dall'ultima cifra della porta del backend.

    Vale solo se il dominio pubblico (X-Forwarded-Host) e' uno dei domini della regola e l'Host
    inoltrato ha una porta. Altrimenti restituisce ''.
    """
    forwarded = environ.get("HTTP_X_FORWARDED_HOST", "").split(",")[0].strip().lower()
    public_host = re.sub(r":\d+$", "", forwarded)
    if not public_host or public_host not in _port_prefix_hosts():
        return ""
    backend = environ.get("HTTP_HOST", "").strip()
    if backend.lower() == forwarded:
        return ""  # il proxy inoltra l'Host pubblico (ProxyPreserveHost On): la porta e' quella pubblica
    port = re.search(r":(\d+)$", backend)
    return f"/api{port.group(1)[-1]}" if port else ""


def collapse_slashes(path):
    """'//data/' -> '/data/'. Apache invia '//' dopo il prefisso con ProxyPass /api1 http://host/."""
    return re.sub(r"/{2,}", "/", path)


def detect(environ):
    """Prefisso della richiesta (es. '/api1'), o '' se non ce n'e'.

    In ordine: header X-Forwarded-Prefix, regola del dominio (porta del backend), percorso.

    Un prefisso non ha segmenti vuoti: il percorso e' normalizzato prima di cercarlo, altrimenti
    '//data/' (Apache) sembrerebbe avere il prefisso '/'.
    """
    header = environ.get("HTTP_X_FORWARDED_PREFIX", "").strip().rstrip("/")
    if header and _valid_header(header):
        return header
    rule = port_rule_prefix(environ)
    if rule:
        return rule
    path = collapse_slashes(environ.get("PATH_INFO", "") or "/")
    if _resolves(path):
        return ""
    # i primi k segmenti (k crescente) sono il prefisso se il resto corrisponde a una pagina
    segments = path.split("/")[1:]
    for k in range(1, len(segments) + 1):
        prefix = "/" + "/".join(segments[:k])
        rest = path[len(prefix):] or "/"
        if _resolves(rest):
            return prefix
    return ""


def relative_prefix(path_info):
    """Prefisso relativo per una pagina: '../' per ogni cartella del percorso ('./' alla radice).

    La pagina /settings/users/ sta in due cartelle: i suoi link partono da '../../'.
    """
    directory = path_info if path_info.endswith("/") else path_info.rsplit("/", 1)[0] + "/"
    depth = directory.count("/") - 1
    return "../" * depth if depth > 0 else "./"


class PrefixApp:
    """Caso A: sposta il prefisso trovato nel percorso in SCRIPT_NAME."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        # Apache con ProxyPass /api1 http://host/ invia //data/: lo normalizzo per tutta l'app
        environ["PATH_INFO"] = collapse_slashes(environ.get("PATH_INFO", ""))
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
