"""Applicazione WSGI dell'interfaccia verso HDN Central (porta 8084).

Espone soltanto /sparql-protected/ e i protocolli firmati della rete HDN sotto
/hdn/; ogni altro percorso risponde 404, cosi' che l'interfaccia di
amministrazione non sia raggiungibile da questa porta. L'applicazione Django
e' costruita una sola volta all'avvio.

Avviata da run_sparql_server.py (supervisord, programma "sparql").
"""

import os

from django.core.wsgi import get_wsgi_application

PROTECTED_PREFIX = "/sparql-protected/"
NETWORK_PREFIX = "/hdn/"
ALLOWED_PREFIXES = (PROTECTED_PREFIX, NETWORK_PREFIX)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
_django = get_wsgi_application()


def application(environ, start_response):
    if environ.get("PATH_INFO", "").startswith(ALLOWED_PREFIXES):
        return _django(environ, start_response)
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"Not Found"]
