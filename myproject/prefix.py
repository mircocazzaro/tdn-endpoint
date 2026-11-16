"""Prefisso di sotto-percorso dell'endpoint (HDN_URL_PREFIX), gestito dentro Django.

Il reverse proxy non toglie nulla: se il sito e' pubblicato come tdn.dei.unipd.it/api1,
la richiesta arriva con PATH_INFO = /api1/settings/. Questo wrapper sposta il
prefisso in SCRIPT_NAME, come farebbe un server dietro un sotto-percorso. Da quel
punto Django instrada su /settings/ e genera URL, redirect e statici con /api1.

Una richiesta che non inizia con il prefisso passa invariata: serve per l'health
check del container, che parla direttamente su 127.0.0.1:8000.
"""


class PrefixApp:
    def __init__(self, app, prefix):
        self.app = app
        self.prefix = (prefix or "").rstrip("/")

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if self.prefix and (path == self.prefix or path.startswith(self.prefix + "/")):
            environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
        return self.app(environ, start_response)
