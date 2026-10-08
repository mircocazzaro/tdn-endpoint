#!/usr/bin/env python3
"""Servizio SPARQL protetto per HDN Central sulla porta 8084.

E' l'unico punto che lo avvia (supervisord, programma "sparql"). In sviluppo,
accanto a "manage.py runserver", va avviato a parte con
"python run_sparql_server.py"; /sparql-protected/ e' comunque servito anche
dal server di sviluppo sulla porta 8000.

Il server serve ogni richiesta in un thread: in modalita' Galois una query
puo' attendere l'LLM per decine di secondi, e non deve bloccare le altre
richieste ne' i protocolli /hdn/.
"""

import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

PORT = int(os.environ.get("HDN_SPARQL_PORT", "8084"))


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


if __name__ == "__main__":
    from myproject.sparql_wsgi import application

    print(f"SPARQL-only server listening on port {PORT}", flush=True)
    make_server("", PORT, application, server_class=ThreadingWSGIServer).serve_forever()
