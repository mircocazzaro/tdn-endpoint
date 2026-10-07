# myproject/settings.py

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',  # Required for admin
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # Required for admin
    'django.contrib.messages.middleware.MessageMiddleware',     # Required for admin
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'myapp.apps.MyappConfig',
    # ...
]

# For simplicity, store uploaded CSVs and the DuckDB file in BASE_DIR / 'uploads'
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'uploads')

# Ensure you have 'django.core.files.uploadhandler.TemporaryFileUploadHandler' in FILE_UPLOAD_HANDLERS
FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]
DEBUG = True  # or whatever your configuration requires
ALLOWED_HOSTS = ['*']
ROOT_URLCONF = 'myproject.urls'


TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # You can customize this if needed
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'myapp.context_processors.level_choices',
            ],
        },
    },
]

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')
# after STATIC_ROOT, etc.
ONTOP_SPARQL_ENDPOINT = 'http://localhost:8080/sparql'
SECRET_KEY = '-dzyj5_ndx2xn7#4-mvxbuxc^2g!=pelj!0l7s$-emri3cog^$'
LEVEL_DB = os.path.join(MEDIA_ROOT, 'level.duckdb')
# Il catalogo delle query ammesse e' codice: myapp/catalog.py.
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

# Audit log locale delle decisioni di disclosure.
# Verso HDN Central rifiuti, template ignoti ed errori di backend sono
# indistinguibili per costruzione; questo file e' l'unico posto in cui
# l'amministratore locale puo' vedere cosa l'endpoint ha effettivamente
# rifiutato e perche'.
AUDIT_LOG = os.environ.get('HDN_AUDIT_LOG', os.path.join(BASE_DIR, 'audit.log'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'audit': {
            'format': '%(asctime)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'audit_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': AUDIT_LOG,
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'audit',
        },
    },
    'loggers': {
        'hdn.audit': {
            'handlers': ['audit_file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(BASE_DIR, "db.sqlite3"),
    }
}

