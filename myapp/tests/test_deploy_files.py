"""Audit-test del punto 40: zip di Ontop duplicato e immagine Docker.

- myapp/obda/ontop-cli-5.3.0.zip (46 MB) era una seconda copia, file per file
  identica, dei 204 file gia' estratti in myapp/obda (lib/, script): ogni
  clone dell'installer la scaricava, e due copie possono divergere.
- Il Dockerfile girava come root, lasciava build-essential nell'immagine, non
  disabilitava la cache di pip e non aveva un healthcheck.
- .dockerignore escludeva solo *.log e *.sqlite3: con COPY . . entravano
  nell'immagine la storia git, uploads/, i database DuckDB popolati e il
  mapping del sito su cui veniva fatto il build.

Docker non e' richiesto per questi test: si verificano i file. Il test di
supervisord senza root gira se il pacchetto supervisor e' installato.
"""

import fnmatch
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)


def _dockerfile():
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def _ignore_patterns():
    return [l.strip() for l in (ROOT / ".dockerignore").read_text().splitlines()
            if l.strip() and not l.startswith("#")]


def _ignored(path):
    for pat in _ignore_patterns():
        p = pat.rstrip("/")
        if path == p or path.startswith(p + "/") or fnmatch.fnmatch(path, p) \
                or fnmatch.fnmatch(Path(path).name, p):
            return True
    return False


class OntopDistributionTests(SimpleTestCase):

    def test_no_second_copy_of_ontop(self):
        obda = ROOT / "myapp" / "obda"
        self.assertEqual(sorted(p.name for p in obda.glob("*.zip")), [])
        self.assertTrue((obda / "lib" / "ontop-cli-5.3.0.jar").is_file())


class DockerfileTests(SimpleTestCase):

    def test_runs_as_an_unprivileged_user(self):
        users = re.findall(r"^USER\s+(\S+)", _dockerfile(), re.M)
        self.assertTrue(users, "nessuna istruzione USER")
        self.assertNotIn(users[-1], ("root", "0"))

    def test_no_compiler_in_the_image(self):
        self.assertNotIn("build-essential", _dockerfile())

    def test_pip_without_cache_and_binary_only(self):
        pip = [l for l in _dockerfile().splitlines() if "pip install" in l]
        self.assertTrue(pip)
        for line in pip:
            self.assertIn("--no-cache-dir", line)
            self.assertIn("--only-binary=:all:", line)

    def test_has_a_healthcheck_on_an_existing_route(self):
        m = re.search(r"^HEALTHCHECK .*?(http://127\.0\.0\.1:8000(/[^'\"]*))", _dockerfile(),
                      re.M | re.S)
        self.assertIsNotNone(m, "nessun HEALTHCHECK HTTP")
        from django.urls import resolve
        resolve(m.group(2))

    def test_supervisord_paths_are_writable_without_root(self):
        conf = (ROOT / "supervisord.conf").read_text()
        section = conf.split("[program:", 1)[0]
        self.assertRegex(section, r"(?m)^logfile=/dev/null$")
        self.assertRegex(section, r"(?m)^pidfile=/tmp/")


class DockerignoreTests(SimpleTestCase):

    def test_local_data_and_history_stay_out_of_the_image(self):
        for path in [".git", "uploads/level.duckdb", "uploads/visits.csv",
                     "myapp/obda/mydatabase.duckdb", "myapp/obda/hereditary_ontology_2.obda",
                     "myapp/obda/ontop.log", "myapp/obda/ontop.log.3", "local-testdata/x.csv",
                     "db.sqlite3", "audit.log", "myapp/obda/ontop.pid"]:
            with self.subTest(path=path):
                self.assertTrue(_ignored(path), f"{path} entrerebbe nell'immagine")

    def test_installer_files_are_kept(self):
        for path in ["manage.py", "requirements.txt", "supervisord.conf",
                     "myapp/catalog.py", "myapp/mappings/template.obda",
                     "myapp/obda/hero_clinical.ttl", "myapp/obda/lib/ontop-cli-5.3.0.jar",
                     "myapp/obda/imports/catalog-v001.xml",
                     "myapp/obda/hereditary_ontology_2.properties",
                     "myapp/static/myapp/vendor/mermaid-11.4.1/mermaid.min.js"]:
            with self.subTest(path=path):
                self.assertFalse(_ignored(path), f"{path} mancherebbe nell'immagine")


class SupervisordWithoutRootTests(SimpleTestCase):
    """Avvia supervisord come utente normale con la sezione [supervisord] del repo."""

    def test_starts_without_root(self):
        supervisord = shutil.which("supervisord") or str(Path(sys.executable).parent / "supervisord")
        if not Path(supervisord).exists():
            self.skipTest("supervisor non installato")
        conf = (ROOT / "supervisord.conf").read_text().split("[program:", 1)[0]
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "ran"
            cfg = Path(d) / "s.conf"
            cfg.write_text(conf + f"\n[program:probe]\ncommand=touch {marker}\n"
                                  "autorestart=false\nstartsecs=0\n")
            proc = subprocess.Popen([supervisord, "-n", "-c", str(cfg)],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for _ in range(50):
                    if marker.exists() or proc.poll() is not None:
                        break
                    time.sleep(0.1)
            finally:
                proc.terminate()
                out = proc.communicate(timeout=20)[0]
            self.assertTrue(marker.exists(), f"supervisord non e' partito:\n{out}")
