# myproject/wsgi.py
import os
from django.conf import settings
from django.core.wsgi import get_wsgi_application

from myproject.prefix import PrefixApp

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
application = PrefixApp(get_wsgi_application(), settings.HDN_URL_PREFIX)
