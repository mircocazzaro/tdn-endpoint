"""Audit-test del punto 31: l'interfaccia non dipende da CDN esterne.

I template caricavano Bootstrap, Bootstrap Icons, jsPlumb e mermaid da
cdn.jsdelivr.net, senza integrita' e, per mermaid, senza versione. In una rete
istituzionale con uscita filtrata l'interfaccia di amministrazione restava
senza stile e senza JavaScript (la pagina di mapping inutilizzabile), e
mermaid poteva cambiare versione senza alcuna modifica al repository.

Ora le librerie sono nel repository, a versione fissata, e i loro hash sono in
myapp/static/myapp/vendor/SHA384SUMS.
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import Client, SimpleTestCase, TestCase

APP = Path(settings.BASE_DIR) / "myapp"
VENDOR = APP / "static" / "myapp" / "vendor"
TEMPLATES = APP / "templates" / "myapp"
PAGES = ["/", "/query/", "/sparql/", "/ontop-control/", "/map-fields/"]

EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//""", re.I)


class VendoredAssetsTests(SimpleTestCase):

    def test_templates_load_nothing_from_external_hosts(self):
        for tpl in sorted(TEMPLATES.glob("*.html")):
            with self.subTest(template=tpl.name):
                self.assertEqual(EXTERNAL.findall(tpl.read_text(encoding="utf-8")), [])

    def test_vendored_files_match_their_hashes(self):
        lines = (VENDOR / "SHA384SUMS").read_text().splitlines()
        self.assertGreaterEqual(len(lines), 8)
        listed = set()
        for line in lines:
            digest, name = line.split("  ", 1)
            listed.add(name)
            with self.subTest(file=name):
                actual = hashlib.sha384((VENDOR / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)
        on_disk = {str(p.relative_to(VENDOR)) for p in VENDOR.rglob("*")
                   if p.is_file() and p.name not in ("SHA384SUMS", "README")}
        self.assertEqual(on_disk, listed, "file in vendor/ non elencati in SHA384SUMS")

    def test_icon_fonts_referenced_by_the_css_exist(self):
        css = (VENDOR / "bootstrap-icons-1.11.1" / "bootstrap-icons.min.css").read_text()
        for ref in re.findall(r'url\("([^"?]+)', css):
            with self.subTest(ref=ref):
                self.assertTrue((VENDOR / "bootstrap-icons-1.11.1" / ref).is_file())


class RenderedPagesTests(TestCase):

    def test_pages_reference_only_static_assets_that_exist(self):
        client = Client()
        for page in PAGES:
            html = client.get(page).content.decode()
            with self.subTest(page=page):
                self.assertEqual(EXTERNAL.findall(html), [])
                for ref in re.findall(r'(?:src|href)="(/static/[^"?#]+)"', html):
                    rel = ref[len(settings.STATIC_URL):]
                    self.assertIsNotNone(finders.find(rel), f"asset mancante: {ref}")
