# myproject/protected_wsgi.py
import os
from django.core.wsgi import get_wsgi_application
from django.urls import set_urlconf

# point at your normal settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

# swap in a tiny URLConf that only has /sparql-protected
set_urlconf("myproject.urls_protected")

application = get_wsgi_application()
