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
# Segreti da ambiente
# ---------------------------------------------------------------------------


def _load_dotenv(path):
    """Legge KEY=VALUE da un file .env (non versionato) nell'ambiente.

    Le variabili gia' presenti nell'ambiente hanno la precedenza. Righe vuote e
    commenti (#) sono ignorati; i valori possono essere fra apici.
    """
    try:
        lines = open(path, encoding='utf-8').read().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(os.environ.get('HDN_ENV_FILE', os.path.join(BASE_DIR, '.env')))

# ---------------------------------------------------------------------------
# Sicurezza
# ---------------------------------------------------------------------------

# SECRET_KEY non e' nel codice. Ordine: HDN_SECRET_KEY (ambiente o .env); se
# manca, una chiave casuale generata al primo avvio e salvata (0600) in
# HDN_STATE_DIR/secret_key, cosi' che tutti i worker e i riavvii usino la stessa
# chiave e nessun valore comune a piu' installazioni esista.
def _secret_key(state_dir):
    value = os.environ.get('HDN_SECRET_KEY', '').strip()
    if value:
        return value
    import secrets
    import time
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, 'secret_key')
    for _ in range(50):
        try:
            with open(path, encoding='utf-8') as fh:
                value = fh.read().strip()
            if value:
                return value
        except FileNotFoundError:
            candidate = secrets.token_urlsafe(64)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass  # un altro worker la sta scrivendo
            else:
                with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                    fh.write(candidate)
                return candidate
        time.sleep(0.05)
    raise RuntimeError('SECRET_KEY non disponibile: imposta HDN_SECRET_KEY')


SECRET_KEY = _secret_key(os.environ.get('HDN_STATE_DIR', os.path.join(MEDIA_ROOT, 'hdn')))

# DEBUG e ALLOWED_HOSTS rientrano nei punti 15 e 2 dell'assessment. Valori invariati.
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
    # Dietro un proxy che toglie il prefisso: URL relativi e redirect assoluti (myapp/proxy.py).
    # Deve avvolgere gli altri, cosi' vede anche i redirect di CommonMiddleware e del login.
    'myapp.proxy.StrippedPrefixMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'myapp.proxy.ProxyAwareCsrfMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Login sull'interfaccia di amministrazione; vedi myapp/middleware.py
    'myapp.middleware.LoginRequiredMiddleware',
]

ROOT_URLCONF = 'myproject.urls'
# Statici: l'URL ha il prefisso pubblico della richiesta (myapp/storage.py)
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'myapp.storage.PrefixedStaticStorage'},
}
# Stesso entrypoint di gunicorn (con il wrapper del prefisso), anche per runserver
WSGI_APPLICATION = 'myproject.wsgi.application'

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


def _instance_id():
    """Identificatore casuale di questa istanza, salvato con lo stato (sopravvive ai riavvii).

    Piu' istanze possono stare sullo stesso dominio (tdn.dei.unipd.it/api0, /api1, ...): senza un
    nome diverso per i cookie, il login in una cancellerebbe la sessione delle altre. Il file si crea
    in modo atomico: i worker che partono insieme leggono lo stesso valore.
    """
    path = os.path.join(HDN_STATE_DIR, 'instance-id')
    try:
        with open(path, encoding='utf-8') as fh:
            value = fh.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass
    import secrets
    candidate = secrets.token_hex(4)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(candidate)
        return candidate
    for _ in range(50):  # un altro worker sta scrivendo: attendi il suo valore
        import time
        time.sleep(0.05)
        with open(path, encoding='utf-8') as fh:
            value = fh.read().strip()
        if value:
            return value
    return candidate


HDN_INSTANCE_ID = _instance_id()
SESSION_COOKIE_NAME = f'hdn_session_{HDN_INSTANCE_ID}'
CSRF_COOKIE_NAME = f'hdn_csrf_{HDN_INSTANCE_ID}'

# Il database (iscrizioni ai Central, notifiche) sta con il resto dello stato,
# cosi' che un solo volume lo conservi.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.path.join(HDN_STATE_DIR, "db.sqlite3"),
    }
}
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

# Accesso all'interfaccia di amministrazione (myapp/accounts.py). La cache dei
# tentativi falliti sta su file, cosi' e' condivisa fra i processi gunicorn.
LOGIN_URL = '/login/'
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.path.join(HDN_STATE_DIR, 'cache'),
    }
}
SESSION_COOKIE_AGE = 8 * 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Policy di disclosure del sito (lavoro dedicato alla privacy, da rivedere).
LEVEL_DB = os.path.join(MEDIA_ROOT, 'level.duckdb')


# ---------------------------------------------------------------------------
# File statici
# ---------------------------------------------------------------------------

STATIC_URL = '/static/'
# WhiteNoise vede il percorso senza prefisso (lo toglie myproject/prefix.py)
WHITENOISE_STATIC_PREFIX = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'static')

# ---------------------------------------------------------------------------
# Ontop
# ---------------------------------------------------------------------------

ONTOP_SPARQL_ENDPOINT = 'http://localhost:8080/sparql'

# ---------------------------------------------------------------------------
# Protocollo hdn-components v1 (POST /sparql-protected/)
# ---------------------------------------------------------------------------

# 'components': la madre si scompone e si risponde con l'involucro v1.
# 'legacy': risposta SPARQL JSON storica (utile se Central non e' ancora
# aggiornato). Le richieste con analytics_key restano sul percorso storico.
HDN_RESPONSE_FORMAT = os.environ.get('HDN_RESPONSE_FORMAT', 'components')
if HDN_RESPONSE_FORMAT not in ('components', 'legacy'):
    raise ValueError("HDN_RESPONSE_FORMAT deve essere 'components' o 'legacy'")
# Timeout di ciascuna componente verso il backend, in secondi.
HDN_COMPONENT_TIMEOUT = int(os.environ.get('HDN_COMPONENT_TIMEOUT', '10'))
# Oltre questo numero di righe totali non si contribuisce (nessun troncamento).
HDN_MAX_COMPONENT_ROWS = int(os.environ.get('HDN_MAX_COMPONENT_ROWS', '200000'))

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
