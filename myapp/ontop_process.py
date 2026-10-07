"""Avvio, arresto e stato del processo Ontop gestito dalla UI di amministrazione.

Usato da Ontop Monitor e dalle scritture sul database dei dati, che devono
arrestare Ontop e riavviarlo (vedi myapp/datastore.py).

Il processo e' identificato da un PID file. Prima di considerarlo acceso, e
prima di mandargli un segnale, si verifica che il PID appartenga davvero a un
processo Ontop vivo:

- un PID file rimasto da un'esecuzione precedente puo' indicare un PID
  riassegnato a un processo qualunque, che non va mai terminato;
- un processo terminato ma non ancora raccolto dal padre (zombie) risponde a
  ``os.kill(pid, 0)`` ma non e' acceso, e non tiene piu' il file DuckDB.
"""

import os
import signal
import subprocess
import time
from pathlib import Path

import requests
from django.conf import settings

ONTOP_DIR = Path(__file__).resolve().parent / "obda"
ONTOP_CMD = ONTOP_DIR / "ontop"
OBDA_FILE = ONTOP_DIR / "hereditary_ontology_2.obda"
TTL_FILE = ONTOP_DIR / "hero_clinical.ttl"
PROPS_FILE = ONTOP_DIR / "hereditary_ontology_2.properties"
PID_FILE = ONTOP_DIR / "ontop.pid"
LOG_FILE = ONTOP_DIR / "ontop.log"

STOP_TIMEOUT = 30.0
START_TIMEOUT = 180.0

# Processi avviati da questo worker, da raccogliere quando terminano.
_children = {}


def read_pid():
    try:
        return int(Path(PID_FILE).read_text().strip())
    except (OSError, ValueError):
        return None


def _clear_pid():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def _proc_field(pid, name):
    try:
        return Path(f"/proc/{pid}/{name}").read_bytes()
    except OSError:
        return None


def _alive(pid):
    """True se ``pid`` e' un processo Ontop vivo."""
    child = _children.get(pid)
    if child is not None and child.poll() is not None:
        _children.pop(pid, None)
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass

    stat = _proc_field(pid, "stat")
    if stat is not None:
        # Il campo di stato segue il nome del processo fra parentesi.
        state = stat.rsplit(b")", 1)[-1].split()[0:1]
        if state == [b"Z"]:
            return False
    cmdline = _proc_field(pid, "cmdline")
    # Un processo che sta terminando ha la cmdline vuota per un attimo prima di
    # diventare zombie: va considerato ancora vivo, perche' puo' non aver
    # ancora rilasciato il file DuckDB. Il controllo sul PID riassegnato vale
    # solo per una cmdline leggibile e non vuota.
    if cmdline and b"ontop" not in cmdline:
        # PID riassegnato: il PID file e' stantio.
        return False
    return True


def is_running():
    pid = read_pid()
    return pid is not None and _alive(pid)


def stop(timeout=None):
    """Arresta Ontop e attende che termini. True se alla fine e' fermo."""
    timeout = STOP_TIMEOUT if timeout is None else timeout
    pid = read_pid()
    if pid is None or not _alive(pid):
        _clear_pid()
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _clear_pid()
        return True

    deadline = time.monotonic() + timeout
    child = _children.get(pid)
    if child is not None:
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        _children.pop(pid, None)
    else:
        while _alive(pid):
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)
    _clear_pid()
    return True


def start():
    """Avvia Ontop se non e' gia' acceso. Restituisce il PID."""
    if is_running():
        return read_pid()
    with open(LOG_FILE, "a") as log:
        log.write(f"\n===== avvio {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()
        proc = subprocess.Popen(
            [str(ONTOP_CMD), "endpoint",
             "-m", str(OBDA_FILE), "-t", str(TTL_FILE), "-p", str(PROPS_FILE)],
            cwd=str(ONTOP_DIR),
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    _children[proc.pid] = proc
    Path(PID_FILE).write_text(str(proc.pid))
    return proc.pid


def wait_ready(timeout=None):
    """Attende che l'endpoint SPARQL risponda. False se Ontop termina o scade il tempo."""
    timeout = START_TIMEOUT if timeout is None else timeout
    pid = read_pid()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid is None or not _alive(pid):
            return False
        try:
            resp = requests.post(
                settings.ONTOP_SPARQL_ENDPOINT,
                data={"query": "ASK {}"},
                headers={"Accept": "application/sparql-results+json"},
                timeout=2,
            )
            if resp.ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False
