"""Audit-test del punto 42: nessun server avviato da AppConfig.ready().

MyappConfig.ready() avviava, con RUN_MAIN=true (cioe' sotto runserver), un
server wsgiref sulla porta 8084 in un thread daemon. Duplicava
run_sparql_server.py, ricostruiva l'applicazione WSGI a ogni richiesta, e se
la porta era gia' occupata (run_sparql_server.py o un secondo runserver) il
thread moriva con OSError senza che nessuno se ne accorgesse.

Ora ready() non ha effetti collaterali e il filtro della porta 8084 e' uno
solo, in myproject/sparql_wsgi.py.
"""

import io
import os
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase


class AppStartupTests(SimpleTestCase):

    def test_ready_starts_no_server(self):
        with mock.patch.dict(os.environ, {"RUN_MAIN": "true"}), \
                mock.patch("wsgiref.simple_server.make_server") as make_server, \
                mock.patch("threading.Thread") as thread:
            apps.get_app_config("myapp").ready()
        make_server.assert_not_called()
        thread.assert_not_called()


class SparqlOnlyAppTests(SimpleTestCase):

    def _call(self, path):
        from myproject import sparql_wsgi
        status = []
        environ = {"PATH_INFO": path, "REQUEST_METHOD": "GET", "SERVER_NAME": "testserver",
                   "SERVER_PORT": "8084", "wsgi.input": io.BytesIO(b""),
                   "wsgi.url_scheme": "http", "wsgi.errors": io.StringIO()}
        body = b"".join(sparql_wsgi.application(environ, lambda s, h: status.append(s)))
        return status[0], body

    def test_admin_pages_are_not_reachable(self):
        for path in ["/", "/query/", "/map-fields/", "/admin/", "/sparql/"]:
            with self.subTest(path=path):
                self.assertEqual(self._call(path)[0], "404 Not Found")

    def test_protected_endpoint_is_served_by_one_django_app(self):
        from myproject import sparql_wsgi
        with mock.patch("myproject.sparql_wsgi.get_wsgi_application") as factory:
            status, _ = self._call("/sparql-protected/")
        self.assertEqual(status, "405 Method Not Allowed")
        factory.assert_not_called()
        self.assertIsNotNone(sparql_wsgi._django)
