"""Applicazione WSGI del servizio SPARQL protetto (porta 8084).

Espone soltanto /sparql-protected/, l'interfaccia verso HDN Central; ogni
altro percorso risponde 404, cosi' che l'interfaccia di amministrazione non
sia raggiungibile da questa porta. L'applicazione Django e' costruita una sola
volta all'avvio.

Avviata da run_sparql_server.py (supervisord, programma "sparql").
"""

import os

from django.core.wsgi import get_wsgi_application

PROTECTED_PREFIX = "/sparql-protected/"

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
_django = get_wsgi_application()


def application(environ, start_response):
    if environ.get("PATH_INFO", "").startswith(PROTECTED_PREFIX):
        return _django(environ, start_response)
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"Not Found"]
