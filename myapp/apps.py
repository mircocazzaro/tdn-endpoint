import os
import threading
from django.apps import AppConfig
from django.conf import settings

class MyappConfig(AppConfig):
    name = 'myapp'

    def ready(self):
        # Only start once, in the “real” run (not during autoreload parent)
        # Django’s runserver sets RUN_MAIN to 'true' in the child that actually serves requests.
        if os.environ.get('RUN_MAIN') != 'true':
            return

        from wsgiref.simple_server import make_server
        from django.core.wsgi import get_wsgi_application

        # Wrap the main WSGI app so it only responds to /sparql-protected/
        def sparql_only_app(environ, start_response):
            path = environ.get('PATH_INFO', '')
            if path.startswith('/sparql-protected/'):
                return get_wsgi_application()(environ, start_response)
            # For any other path, return 404
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'Not Found']

        def run_sparql_server():
            httpd = make_server('', 8084, sparql_only_app)
            # Optional: log to console
            print("🔊 SPARQL‐only server listening on port 8084")
            httpd.serve_forever()

        # Launch in a background daemon thread
        t = threading.Thread(target=run_sparql_server, daemon=True)
        t.start()
