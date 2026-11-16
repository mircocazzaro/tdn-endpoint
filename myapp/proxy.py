"""Reverse proxy che toglie il prefisso: redirect, URL relativi e CSRF (vedi myproject/prefix.py).

Si attiva solo quando la richiesta arriva da un proxy (header X-Forwarded-Host, che
Apache e nginx impostano) e il percorso non contiene un prefisso gia' riconosciuto.
Senza proxy, e quando il prefisso e' nel percorso, non fa nulla.
"""
import re
from urllib.parse import urljoin, urlsplit

from django.middleware.csrf import CsrfViewMiddleware
from django.urls import set_script_prefix

from myproject.prefix import current_prefix, relative_prefix

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def behind_stripping_proxy(request):
    """True se arriva da un proxy e nessun prefisso e' stato riconosciuto nel percorso."""
    return bool(request.META.get("HTTP_X_FORWARDED_HOST")) and not current_prefix()


class StrippedPrefixMiddleware:
    """Dietro un proxy che toglie il prefisso: URL relativi nelle pagine, redirect assoluti.

    I link devono funzionare sotto qualunque prefisso, quindi reverse() produce URL
    relativi alla pagina. I redirect vanno resi assoluti verso l'Host che il proxy ha
    inoltrato (l'indirizzo del backend): ProxyPassReverse li riscrive con il prefisso,
    mentre un Location che parte da '/' non verrebbe toccato e perderebbe il prefisso.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stripped = behind_stripping_proxy(request)
        if stripped:
            set_script_prefix(relative_prefix(request.path_info))
        response = self.get_response(request)
        if stripped and 300 <= response.status_code < 400 and response.has_header("Location"):
            response["Location"] = absolute_backend_url(request, response["Location"])
        return response


def absolute_backend_url(request, location):
    """Location assoluto sull'indirizzo del backend, risolvendo un riferimento relativo.

    Con ``ProxyPass /api1 http://127.0.0.1:8071/`` e ``ProxyPassReverse /api1 http://127.0.0.1:8071/``
    Apache sostituisce l'inizio ``http://127.0.0.1:8071/`` con ``/api1``: lo slash che segue l'host
    fa parte di cio' che viene sostituito, quindi ``http://127.0.0.1:8071/login/`` diventa
    ``/api1login/``. Uno slash in piu' (``http://127.0.0.1:8071//login/``) ne restituisce uno solo
    dopo la sostituzione. Verificato con Apache 2.4; senza proxy che riscrive, il doppio slash
    iniziale e' ignorato dai server, che lo trattano come '/'.
    """
    if _SCHEME_RE.match(location) or location.startswith("//"):
        return location
    path = urljoin(request.path_info, location)
    # l'Host grezzo: e' l'indirizzo che ProxyPassReverse conosce, non quello pubblico
    host = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME", "")
    return f"{request.scheme}://{host}/{path}"


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
    """Radice del sito.

    Dietro un proxy che toglie il prefisso il browser puo' essere su /api1 (senza slash) o su
    /api1/: il backend li vede uguali, ma gli URL relativi si risolvono in modo diverso. Si va a
    una pagina con lo slash finale, con un redirect assoluto che il proxy riscrive.
    Altrove la radice e' la home, come sempre.
    """
    from django.shortcuts import redirect
    from myapp.views import home_view
    if behind_stripping_proxy(request):
        return redirect("home_page")
    return home_view(request)
