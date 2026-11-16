"""Login obbligatorio per l'interfaccia di amministrazione (porta 8000).

Restano pubblici solo i percorsi che hanno gia' un proprio controllo o che
servono a servizi esterni:

- /hdn/ e /sparql-protected/: protocolli firmati e risposta canonica di Central;
- /ontop/status/: health check del container, che non espone dati;
- /static/: file statici;
- login, logout e primo avvio, che servono a entrare.

Finche' non esiste nessun utente, ogni richiesta protetta va al primo avvio, che
crea il primo amministratore.
"""
from urllib.parse import quote

from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import reverse

PUBLIC_PREFIXES = (
    "/static/",
    "/hdn/",
    "/sparql-protected/",
    "/ontop/status/",
    "/login/",
    "/logout/",
    "/setup/",
)


def is_public(path):
    return path.startswith(PUBLIC_PREFIXES)


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # path_info, non path: con un prefisso pubblico path lo contiene e le pagine
        # pubbliche non verrebbero riconosciute, con un login in loop.
        if is_public(request.path_info) or request.user.is_authenticated:
            return self.get_response(request)
        if not get_user_model().objects.exists():
            return redirect("setup")
        return redirect(f"{reverse('login')}?next={quote(request.get_full_path())}")
