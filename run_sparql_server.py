#!/usr/bin/env python3
import os
from wsgiref.simple_server import make_server
from django.core.wsgi import get_wsgi_application

# 1) Init Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
app = get_wsgi_application()

# 2) Wrap it to only allow /sparql-protected/
def sparql_only_app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if path.startswith("/sparql-protected/"):
        return app(environ, start_response)
    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"Not Found"]

# 3) Run the simple server
if __name__ == "__main__":
    print("🔊 SPARQL‐only server listening on port 8084")
    httpd = make_server("", 8084, sparql_only_app)
    httpd.serve_forever()
