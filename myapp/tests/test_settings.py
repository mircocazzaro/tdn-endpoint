"""Audit-test del punto 41: settings.py ordinato e impostazioni HTTPS disponibili.

Prima: BASE_DIR e gli import erano definiti due volte, MIDDLEWARE veniva
modificato con insert() dopo la definizione (l'ordine reale non era leggibile
nel punto in cui era dichiarato), DATABASES stava in fondo dopo impostazioni
non correlate, e non esisteva modo di attivare cookie Secure, redirect HTTPS e
HSTS: con l'endpoint servito in HTTPS, manage.py check --deploy segnalava
W004, W008, W012, W016.

Ora ogni impostazione e' definita una volta e le impostazioni HTTPS si
attivano con HDN_HTTPS=1. Restano attese W018 (DEBUG, punto 15) e, con HTTPS,
W005/W021 (HSTS su tutti i sottodomini e preload, volutamente spenti: il
dominio di un ospedale non appartiene all'endpoint).
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)
SETTINGS = ROOT / "myproject" / "settings.py"


def _deploy_warnings(https):
    env = dict(os.environ, HDN_HTTPS="1" if https else "")
    r = subprocess.run([sys.executable, "manage.py", "check", "--deploy"], cwd=ROOT,
                       capture_output=True, text=True, env=env, timeout=120)
    return set(re.findall(r"security\.W\d+", r.stdout + r.stderr))


class SettingsStructureTests(SimpleTestCase):

    def test_every_setting_and_import_appears_once(self):
        tree = ast.parse(SETTINGS.read_text(encoding="utf-8"))
        names, imports = [], []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names += [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.append(ast.dump(node))
        self.assertEqual(sorted({n for n in names if names.count(n) > 1}), [])
        self.assertEqual(len(imports), len(set(imports)), "import ripetuti")

    def test_settings_are_not_mutated_after_definition(self):
        text = SETTINGS.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^[A-Z_]+\.(insert|append|extend|remove)\(", text, re.M))

    def test_middleware_order(self):
        mw = settings.MIDDLEWARE
        self.assertEqual(mw[0], "django.middleware.security.SecurityMiddleware")
        self.assertEqual(mw[1], "whitenoise.middleware.WhiteNoiseMiddleware")


class HttpsSettingsTests(SimpleTestCase):

    def test_https_warnings_are_resolved_when_enabled(self):
        self.assertEqual(_deploy_warnings(https=True) & {"security.W004", "security.W008",
                                                       "security.W012", "security.W016"}, set())

    def test_http_default_keeps_cookies_usable(self):
        """Senza HDN_HTTPS i cookie non sono Secure: l'interfaccia in HTTP funziona."""
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
