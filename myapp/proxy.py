"""Reverse proxy che toglie il prefisso: redirect, URL relativi e CSRF (vedi myproject/prefix.py).

Si attiva solo quando la richiesta arriva da un proxy (header X-Forwarded-Host, che
Apache e nginx impostano) e il percorso non contiene un prefisso gia' riconosciuto.
Senza proxy, e quando il prefisso e' nel percorso, non fa nulla.

Il meccanismo non dipende ne' dall'Host inoltrato ne' da ProxyPassReverse: ogni URL che l'applicazione
produce e' relativo alla pagina che lo contiene, e il browser lo risolve sotto qualunque prefisso.
"""
import posixpath
import re
from urllib.parse import urljoin, urlsplit

from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.urls import set_script_prefix

from myproject.prefix import current_prefix, relative_prefix

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")

# Radice dietro un proxy che toglie il prefisso. Per il backend /api1 e /api1/ sono la stessa richiesta,
# ma il browser li risolve in modo diverso: lo slash finale lo sistema il browser, che conosce il suo URL.
ROOT_PAGE = (
    "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Trusted Data Network</title></head><body>"
    "<script>var p=location.pathname;if(p.charAt(p.length-1)!=='/')p+='/';"
    "location.replace(p+'home/'+location.search);</script>"
    "<noscript><a href=\"home/\">Continue</a></noscript></body></html>"
)


def behind_stripping_proxy(request):
    """True se arriva da un proxy e nessun prefisso e' stato riconosciuto nel percorso."""
    return bool(request.META.get("HTTP_X_FORWARDED_HOST")) and not current_prefix()


class StrippedPrefixMiddleware:
    """Dietro un proxy che toglie il prefisso: URL e redirect relativi alla pagina.

    reverse() usa come prefisso '../' ripetuto per la profondita' del percorso, e i redirect sono
    riscritti come riferimenti relativi alla pagina corrente (relative_location). La radice e' una
    pagina che aggiunge lo slash finale e passa a /home/: il backend non distingue /api1 da /api1/.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stripped = behind_stripping_proxy(request)
        if stripped:
            if request.path_info == "/" and request.method in ("GET", "HEAD"):
                response = HttpResponse(ROOT_PAGE, content_type="text/html; charset=utf-8")
                response["Cache-Control"] = "no-store"
                return response
            set_script_prefix(relative_prefix(request.path_info))
        response = self.get_response(request)
        if stripped and 300 <= response.status_code < 400 and response.has_header("Location"):
            response["Location"] = relative_location(request, response["Location"])
        return response


def relative_location(request, location):
    """Location come riferimento relativo alla pagina corrente.

    Da /data/ il redirect a /login/?next=/data/ diventa '../login/?next=/data/': il browser lo
    risolve rispetto all'URL pubblico (/api1/data/) e arriva a /api1/login/, qualunque sia il prefisso.
    Gli indirizzi assoluti e quelli di un altro sito restano com'e'.
    """
    if _SCHEME_RE.match(location) or location.startswith("//"):
        return location
    target = urlsplit(urljoin(request.path_info, location))
    path = target.path or "/"
    here = request.path_info
    base = here if here.endswith("/") else here.rsplit("/", 1)[0] + "/"
    rel = posixpath.relpath(path, base)
    if rel == ".":
        rel = "./"
    elif path.endswith("/"):
        rel += "/"
    if ":" in rel.split("/", 1)[0]:
        rel = "./" + rel  # un segmento con ':' si leggerebbe come uno schema
    return rel + (f"?{target.query}" if target.query else "") + (f"#{target.fragment}" if target.fragment else "")


class ProxyAwareCsrfMiddleware(CsrfViewMiddleware):
    """CSRF che riconosce il sito pubblico quando l'Host e' quello del backend.

    Il browser invia Origin = sito pubblico, mentre l'Host che il proxy inoltra e' il backend, e
    il controllo standard rifiuterebbe ogni POST. Dietro un proxy si confronta l'host dell'Origin
    con X-Forwarded-Host, senza lo schema (il proxy puo' terminare TLS senza dirlo). Un'origine
    di un altro dominio resta rifiutata.
    """

    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True
        forwarded = request.META.get("HTTP_X_FORWARDED_HOST", "").split(",")[0].strip()
        if not forwarded:
            return False
        try:
            origin = urlsplit(request.META["HTTP_ORIGIN"])
        except ValueError:
            return False
        return origin.scheme in ("http", "https") and origin.netloc.lower() == forwarded.lower()


def root_view(request):
    """Radice del sito: la home. Dietro un proxy che toglie il prefisso la risponde il middleware."""
    from myapp.views import home_view
    return home_view(request)
