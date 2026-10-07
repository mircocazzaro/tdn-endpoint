"""Audit-test del punto 11: accesso al database dei dati con Ontop acceso.

Prima della correzione Ontop apriva ``mydatabase.duckdb`` in lettura-scrittura
(default del driver JDBC) e lo teneva aperto per tutta la vita dell'endpoint.
DuckDB ammette un solo processo in scrittura, che esclude anche i lettori:
con Ontop acceso, cioe' nello stato normale di un endpoint in produzione,
home, mapping, get-columns, query relazionali, upload e cancellazione di
tabelle fallivano con ``Could not set lock on file``, che le view non
gestivano (HTTP 500).

Verificato a mano con Ontop 5.3.0 reale, su una copia dei dati:

    properties attuale       -> Django non apre il file, ne' RW ne' RO
    + duckdb.read_only=true  -> Django apre in RO, Ontop risponde alle query

Questi test riproducono la stessa situazione con un processo separato che
tiene il file aperto come Ontop: con il driver JDBC di Ontop e le proprieta'
lette dal suo .properties quando Java e' disponibile, altrimenti con DuckDB in
Python, che ha le stesse regole di lock.
"""

import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.conf import settings
from django.contrib.messages import get_messages
from django.test import Client, TestCase, override_settings

from myapp import datastore, ontop_process, views

OBDA_DIR = Path(settings.BASE_DIR) / "myapp" / "obda"
PROPS_FILE = OBDA_DIR / "hereditary_ontology_2.properties"
JDBC_JAR = OBDA_DIR / "jdbc" / "duckdb_jdbc-1.1.3.jar"
HOLD_LOCK = Path(__file__).resolve().parent / "fixtures" / "HoldLock.java"
JAVA = shutil.which("java")

_PY_HOLDER = (
    "import sys, duckdb\n"
    "con = duckdb.connect(sys.argv[1], read_only=sys.argv[2] == 'ro')\n"
    "print('READY', flush=True)\n"
    "sys.stdin.readline()\n"
    "con.close()\n"
)


def _ontop_properties():
    props = {}
    for line in PROPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
    return props


class Holder:
    """Processo che tiene aperto il database, come Ontop."""

    def __init__(self, db, read_only=True, use_ontop_driver=False, props_file=PROPS_FILE):
        if use_ontop_driver:
            args = [JAVA, "-cp", str(JDBC_JAR), str(HOLD_LOCK), str(props_file), str(db)]
        else:
            args = [sys.executable, "-c", _PY_HOLDER, str(db), "ro" if read_only else "rw"]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True)
        line = self.proc.stdout.readline()
        if not line.startswith("READY"):
            self.proc.kill()
            raise RuntimeError("holder non avviato: " + line + self.proc.stdout.read())

    @property
    def alive(self):
        return self.proc.poll() is None

    def release(self):
        if self.alive:
            self.proc.communicate("\n", timeout=60)


def _make_db(path):
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE "patients" (pid VARCHAR, sex VARCHAR)')
    con.execute("INSERT INTO \"patients\" VALUES ('p1', 'female')")
    con.close()


class _DataDbTestCase(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "mydatabase.duckdb"
        _make_db(self.db)
        for name, value in [("DUCKDB_PATH", str(self.db))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)
        self.client = Client()

    def hold(self, **kw):
        holder = Holder(self.db, **kw)
        self.addCleanup(holder.release)
        return holder


class OntopConfigurationTests(TestCase):

    def test_ontop_opens_the_data_read_only(self):
        props = _ontop_properties()
        self.assertEqual(props.get("jdbc.property.duckdb.read_only"), "true",
                         "Ontop non e' configurato per aprire i dati in sola lettura")
        self.assertEqual(props.get("jdbc.driver"), "org.duckdb.DuckDBDriver")


class AdminReadsWithOntopRunningTests(_DataDbTestCase):
    """Le pagine che leggono i dati funzionano mentre Ontop tiene il file."""

    def _assert_admin_pages_read_the_data(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("patients", home.context["tables_columns"])

        cols = self.client.get("/get-columns/", {"table": "patients"})
        self.assertEqual(cols.status_code, 200)
        self.assertEqual(cols.json()["columns"], ["pid", "sex"])

        query = self.client.post("/query/", {"sql_query": "SELECT count(*) FROM patients"})
        self.assertEqual(query.status_code, 200)
        self.assertIsNone(query.context["error"])
        self.assertEqual(query.context["results"], [[1]])

    def test_reads_with_a_read_only_holder(self):
        self.hold(read_only=True)
        self._assert_admin_pages_read_the_data()

    def test_reads_with_the_ontop_jdbc_driver_and_its_properties(self):
        """Lo scenario di produzione: il driver di Ontop con il .properties reale."""
        if not (JAVA and JDBC_JAR.exists()):
            self.skipTest("Java o il driver JDBC di Ontop non disponibili")
        self.hold(use_ontop_driver=True)
        self._assert_admin_pages_read_the_data()

    def test_previous_ontop_configuration_reproduces_the_lock(self):
        """Controllo negativo: con il .properties precedente il conflitto c'e'.

        Senza read_only il driver di Ontop tiene il file in scrittura e Django
        non riesce ad aprirlo neppure in sola lettura. Dimostra che l'ambiente
        di test riproduce davvero il problema, e quindi che il test precedente
        prova qualcosa.
        """
        if not (JAVA and JDBC_JAR.exists()):
            self.skipTest("Java o il driver JDBC di Ontop non disponibili")
        old_props = self.tmp / "previous.properties"
        old_props.write_text("jdbc.url = jdbc:duckdb:mydatabase.duckdb\n"
                             "jdbc.driver = org.duckdb.DuckDBDriver\n")
        self.hold(use_ontop_driver=True, props_file=old_props)

        with self.assertRaises(duckdb.IOException):
            duckdb.connect(str(self.db), read_only=True)

    def test_a_held_database_is_reported_not_crashed(self):
        """Se il file e' tenuto in scrittura da altri, la pagina lo dice invece di un 500."""
        self.hold(read_only=False)
        with mock.patch.object(datastore, "READ_TIMEOUT", 0.3):
            home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        levels = [m.level_tag for m in home.context["messages"]]
        self.assertIn("warning", levels)

    def test_missing_database_shows_an_empty_schema(self):
        self.db.unlink()
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.context["tables_columns"], {})
        self.assertFalse(self.db.exists(), "una lettura non deve creare il database")


class FakeOntop:
    """Sostituisce ontop_process con un processo che tiene il file in sola lettura."""

    def __init__(self, test, running=True, stops=True, restarts=True):
        self.test = test
        self.stops = stops
        self.restarts = restarts
        self.calls = []
        self.holder = test.hold(read_only=True) if running else None

    def is_running(self):
        return self.holder is not None and self.holder.alive

    def stop(self, timeout=None):
        self.calls.append("stop")
        if self.stops and self.holder is not None:
            self.holder.release()
        return self.stops

    def start(self):
        self.calls.append("start")
        if self.restarts:
            self.holder = self.test.hold(read_only=True)

    def wait_ready(self, timeout=None):
        self.calls.append("wait_ready")
        return self.restarts

    def install(self):
        for name in ("is_running", "stop", "start", "wait_ready"):
            patcher = mock.patch.object(ontop_process, name, getattr(self, name))
            patcher.start()
            self.test.addCleanup(patcher.stop)
        return self


def _csv(name, text="pid,age\np9,61\n"):
    f = io.BytesIO(text.encode("utf-8"))
    f.name = name
    return f


class WritesWithOntopRunningTests(_DataDbTestCase):
    """Upload e cancellazione arrestano Ontop, scrivono e lo riavviano."""

    def setUp(self):
        super().setUp()
        media = override_settings(MEDIA_ROOT=str(self.tmp / "media"))
        media.enable()
        self.addCleanup(media.disable)
        (self.tmp / "media").mkdir()
        short = mock.patch.object(datastore, "WRITE_TIMEOUT", 1.0)
        short.start()
        self.addCleanup(short.stop)

    def _messages(self, response):
        return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]

    def _tables(self):
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            return {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()

    def test_upload_with_ontop_running(self):
        ontop = FakeOntop(self).install()

        resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ontop.calls, ["stop", "start", "wait_ready"])
        self.assertTrue(ontop.is_running(), "Ontop non e' stato riavviato")
        ontop.holder.release()
        self.assertIn("visits", self._tables())
        self.assertEqual([lvl for lvl, _ in self._messages(resp)],
                         ["info", "success", "success"])
        texts = " | ".join(t for _, t in self._messages(resp))
        self.assertIn("stopped", texts)
        self.assertIn("Tables updated: visits", texts)
        self.assertIn("restarted", texts)

    def test_upload_with_ontop_stopped_does_not_touch_ontop(self):
        ontop = FakeOntop(self, running=False).install()

        resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertEqual(ontop.calls, [])
        self.assertIn("visits", self._tables())
        self.assertEqual([lvl for lvl, _ in self._messages(resp)], ["success"])

    def test_upload_without_stopping_ontop_would_fail(self):
        """Controllo negativo: se Ontop non viene arrestato la scrittura non passa.

        stop() riporta successo ma lascia il file aperto. La scrittura deve
        fallire sul lock, nessuna tabella deve essere creata, e Ontop deve
        comunque risultare riavviato.
        """
        ontop = FakeOntop(self).install()

        def stop_that_leaves_the_file_open(timeout=None):
            ontop.calls.append("stop")
            return True

        with mock.patch.object(ontop_process, "stop", stop_that_leaves_the_file_open):
            resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertEqual(resp.status_code, 302)
        ontop.holder.release()
        self.assertNotIn("visits", self._tables())
        levels = [lvl for lvl, _ in self._messages(resp)]
        self.assertIn("error", levels)
        self.assertIn("wait_ready", ontop.calls)

    def test_ontop_that_does_not_stop_blocks_the_write(self):
        ontop = FakeOntop(self, stops=False).install()

        resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertEqual(ontop.calls, ["stop"], "senza arresto non si deve scrivere ne' riavviare")
        ontop.holder.release()
        self.assertNotIn("visits", self._tables())
        self.assertEqual([lvl for lvl, _ in self._messages(resp)], ["error"])

    def test_failed_ingestion_still_restarts_ontop(self):
        ontop = FakeOntop(self).install()

        with mock.patch.object(datastore, "write_connection",
                               side_effect=RuntimeError("disco pieno")):
            resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertEqual(ontop.calls, ["stop", "start", "wait_ready"])
        self.assertTrue(ontop.is_running())
        self.assertEqual([lvl for lvl, _ in self._messages(resp)],
                         ["info", "error", "success"])

    def test_failed_restart_is_reported(self):
        FakeOntop(self, restarts=False).install()

        resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")})

        self.assertIn("visits", self._tables())
        msgs = self._messages(resp)
        self.assertEqual([lvl for lvl, _ in msgs], ["info", "success", "error"])
        self.assertIn("did not restart", msgs[-1][1])

    def test_delete_table_with_ontop_running(self):
        ontop = FakeOntop(self).install()

        resp = self.client.post("/delete-table/patients/")

        self.assertEqual(ontop.calls, ["stop", "start", "wait_ready"])
        ontop.holder.release()
        self.assertNotIn("patients", self._tables())
        self.assertEqual([lvl for lvl, _ in self._messages(resp)],
                         ["info", "success", "success"])

    def test_deleting_an_unknown_table_does_not_restart_ontop(self):
        ontop = FakeOntop(self).install()

        resp = self.client.post("/delete-table/nonexistent/")

        self.assertEqual(ontop.calls, [])
        self.assertEqual([lvl for lvl, _ in self._messages(resp)], ["error"])

    def test_toasts_are_rendered_with_their_level(self):
        """Gli errori sono rossi, restano visibili e sono annunciati come alert."""
        FakeOntop(self, restarts=False).install()

        resp = self.client.post("/upload-csv/", {"csv_files": _csv("visits.csv")},
                                follow=True)

        html = resp.content.decode("utf-8")
        self.assertEqual(html.count('class="toast hdn-toast'), 3)
        self.assertIn("text-bg-info", html)
        self.assertIn("text-bg-success", html)
        self.assertIn("text-bg-danger", html)
        error_toast = html[html.index("text-bg-danger"):].split("</div>", 1)[0]
        self.assertIn('role="alert"', error_toast)
        self.assertIn('data-bs-autohide="false"', error_toast)
        self.assertNotIn("alert-success", html, "messaggi ancora resi come banner verdi")


class OntopProcessTests(TestCase):
    """Il PID file non deve mai portare a terminare un processo che non e' Ontop."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pid_file = Path(self._tmp.name) / "ontop.pid"
        patcher = mock.patch.object(ontop_process, "PID_FILE", pid_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pid_file = pid_file

    def test_stale_pid_of_another_process_is_not_running_and_is_not_signalled(self):
        # Un processo vivo che non e' Ontop: il PID file e' stantio.
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(other.kill)
        self.pid_file.write_text(str(other.pid))

        self.assertFalse(ontop_process.is_running())
        self.assertTrue(ontop_process.stop(timeout=1))
        self.assertIsNone(other.poll(), "e' stato terminato un processo che non e' Ontop")
        self.assertFalse(self.pid_file.exists())

    def test_running_ontop_is_stopped(self):
        fake = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "ontop-endpoint-fake"])
        self.addCleanup(fake.kill)
        ontop_process._children[fake.pid] = fake
        self.pid_file.write_text(str(fake.pid))

        self.assertTrue(ontop_process.is_running())
        self.assertTrue(ontop_process.stop(timeout=10))
        self.assertIsNotNone(fake.poll())
        self.assertFalse(ontop_process.is_running())

    def test_missing_or_garbage_pid_file(self):
        self.assertFalse(ontop_process.is_running())
        self.pid_file.write_text("non-un-pid")
        self.assertFalse(ontop_process.is_running())
        self.assertTrue(ontop_process.stop(timeout=1))

    def test_stop_waits_for_a_process_that_is_not_our_child(self):
        """Ontop avviato da un altro worker: niente wait(), si attende la fine via /proc.

        Il processo intercetta SIGTERM e impiega un secondo a uscire, come Ontop
        che chiude la JVM: stop() non deve dichiararlo fermo prima.
        """
        script = ("import signal, sys, time\n"
                  "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1), sys.exit(0)))\n"
                  "print('READY', flush=True)\n"
                  "time.sleep(60)\n")
        slow = subprocess.Popen([sys.executable, "-c", script, "ontop-endpoint-fake"],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(slow.kill)
        slow.stdout.readline()
        self.pid_file.write_text(str(slow.pid))

        self.assertTrue(ontop_process.stop(timeout=10))
        # Al ritorno di stop() il processo ha gia' terminato l'uscita.
        slow.wait(timeout=2)
        self.assertEqual(slow.returncode, 0)
