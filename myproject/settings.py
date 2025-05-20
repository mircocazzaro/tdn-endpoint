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
ALLOWED_DB = os.path.join(MEDIA_ROOT, 'allowed_queries.duckdb')
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

