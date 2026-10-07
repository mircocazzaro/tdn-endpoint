"""Impostazioni Django dell'HDN Endpoint.

Ogni impostazione e' definita una sola volta, in quest'ordine: percorsi,
sicurezza, applicazione, dati, file statici, integrazione con Ontop, log.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

# CSV caricati dall'amministratore e stato locale del sito.
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'uploads')

# ---------------------------------------------------------------------------
# Sicurezza
# ---------------------------------------------------------------------------

# Da rendere configurabili per sito: SECRET_KEY va ruotata e letta
# dall'ambiente, DEBUG e ALLOWED_HOSTS rientrano nei punti 15 e 2
# dell'assessment. Valori invariati.
SECRET_KEY = '-dzyj5_ndx2xn7#4-mvxbuxc^2g!=pelj!0l7s$-emri3cog^$'
DEBUG = True
ALLOWED_HOSTS = ['*']

# HTTPS. Attivo con HDN_HTTPS=1 quando l'endpoint e' servito in HTTPS, anche
# dietro un reverse proxy che termina TLS e imposta X-Forwarded-Proto.
# Spento di default: su un endpoint servito in HTTP su rete interna, cookie di
# sessione e CSRF marcati Secure non verrebbero mai inviati dal browser e ogni
# form dell'interfaccia fallirebbe.
HTTPS = os.environ.get('HDN_HTTPS') == '1'
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
SECURE_SSL_REDIRECT = HTTPS
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if HTTPS else None

# ---------------------------------------------------------------------------
# Applicazione
# ---------------------------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'myapp.apps.MyappConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'myproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'myapp.context_processors.level_choices',
                'myapp.context_processors.notifications',
            ],
        },
    },
]

# Gli upload passano da file temporanei anziche' dalla memoria: i CSV dei
# siti possono essere grandi.
FILE_UPLOAD_HANDLERS = [
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

# ---------------------------------------------------------------------------
# Dati
# ---------------------------------------------------------------------------

# Database dell'applicazione Django (sessioni, admin). I dati del sito sono
# in DuckDB, vedi myapp/datastore.py; il catalogo delle query ammesse e' codice,
# myapp/catalog.py.
# Stato dell'endpoint nella rete HDN: chiave privata del nodo, catalogo e
# ontologia ricevuti da Central, copie di sicurezza. Non versionato.
HDN_STATE_DIR = os.environ.get('HDN_STATE_DIR', os.path.join(MEDIA_ROOT, 'hdn'))
os.makedirs(HDN_STATE_DIR, exist_ok=True)

# Il database (iscrizioni ai Central, notifiche) sta con il resto dello stato,
# cosi' che un solo volume lo conservi.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(HDN_STATE_DIR, "db.sqlite3"),
    }
}
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Policy di disclosure del sito (lavoro dedicato alla privacy, da rivedere).
LEVEL_DB = os.path.join(MEDIA_ROOT, 'level.duckdb')


# ---------------------------------------------------------------------------
# File statici
# ---------------------------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# ---------------------------------------------------------------------------
# Ontop
# ---------------------------------------------------------------------------

ONTOP_SPARQL_ENDPOINT = 'http://localhost:8080/sparql'

# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------

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
