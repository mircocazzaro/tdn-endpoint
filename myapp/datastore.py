"""Accesso al database DuckDB dei dati locali (myapp/obda/mydatabase.duckdb).

DuckDB e' un motore embedded e ammette, su un file:

- un solo processo in lettura-scrittura, che esclude ogni altro processo,
  anche in sola lettura;
- piu' processi in sola lettura contemporaneamente;
- nello stesso processo, connessioni con configurazione diversa (una in
  sola lettura e una in scrittura) non possono coesistere.

Ontop apre il file in sola lettura (``jdbc.property.duckdb.read_only`` in
hereditary_ontology_2.properties) e lo tiene aperto per tutta la vita
dell'endpoint. Da qui le regole di questo modulo:

- ogni lettura della UI di amministrazione apre in sola lettura, e quindi
  convive con Ontop acceso;
- le scritture (upload, eliminazione di tabelle) richiedono che Ontop sia
  fermo: la view lo arresta e lo riavvia attorno alla scrittura;
- un lock di processo serializza le connessioni dei thread dello stesso
  worker, cosi' che una lettura e una scrittura concorrenti non si scontrino
  sulla configurazione.

Quando il file e' tenuto da un altro processo le funzioni attendono per un
tempo limitato e poi sollevano DataStoreBusy, invece di propagare un errore
di I/O che la view trasformerebbe in un 500.
"""

import os
import threading
import time
from contextlib import contextmanager

import duckdb

_PROCESS_LOCK = threading.RLock()

READ_TIMEOUT = 2.0
WRITE_TIMEOUT = 30.0
_RETRY_INTERVAL = 0.2


class DataStoreBusy(RuntimeError):
    """Il file e' tenuto da un altro processo oltre il tempo di attesa."""


def _is_lock_conflict(exc):
    text = str(exc)
    return ("Could not set lock" in text
            or "Conflicting lock" in text
            or "different configuration" in text)


def _connect(path, read_only, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return duckdb.connect(path, read_only=read_only)
        except (duckdb.IOException, duckdb.ConnectionException) as exc:
            if not _is_lock_conflict(exc):
                raise
            if time.monotonic() >= deadline:
                raise DataStoreBusy(str(exc).splitlines()[0]) from exc
            time.sleep(_RETRY_INTERVAL)


@contextmanager
def read_connection(path, timeout=None):
    """Connessione in sola lettura, o None se il database non esiste ancora.

    In sola lettura DuckDB non crea il file: un endpoint appena installato,
    senza tabelle caricate, deve mostrare uno schema vuoto e non un errore.
    """
    if not os.path.exists(path):
        yield None
        return
    with _PROCESS_LOCK:
        con = _connect(path, read_only=True,
                       timeout=READ_TIMEOUT if timeout is None else timeout)
        try:
            yield con
        finally:
            con.close()


@contextmanager
def write_connection(path, timeout=None):
    """Connessione in lettura-scrittura; crea il file se non esiste.

    L'attesa copre anche il rilascio del file da parte di Ontop appena
    arrestato.
    """
    with _PROCESS_LOCK:
        con = _connect(path, read_only=False,
                       timeout=WRITE_TIMEOUT if timeout is None else timeout)
        try:
            yield con
        finally:
            con.close()


def _schema(con):
    """{tabella: [(colonna, tipo)]} in ordine, letto senza interpolare nomi.

    I nomi di tabella vengono dai CSV caricati: interpolarli in
    PRAGMA table_info('{t}') faceva fallire la lettura dello schema (e la
    home) con un apice nel nome.
    """
    schema = {}
    for table, column, sql_type in con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "ORDER BY table_name, ordinal_position").fetchall():
        schema.setdefault(table, []).append((column, sql_type))
    return schema


def tables_columns(path, timeout=None):
    """Schema del database: {tabella: [colonne]}, vuoto se il file non esiste."""
    with read_connection(path, timeout=timeout) as con:
        if con is None:
            return {}
        return {t: [name for name, _ in cols] for t, cols in _schema(con).items()}


def column_types(path, timeout=None):
    """Tipi delle colonne: {tabella: {colonna: tipo DuckDB}}, vuoto se il file non esiste."""
    with read_connection(path, timeout=timeout) as con:
        if con is None:
            return {}
        return {t: dict(cols) for t, cols in _schema(con).items()}


def failing_sources(path, sources, timeout=None):
    """Esegue ogni source sul database del sito, in sola lettura.

    ``sources`` e' una lista di coppie (id, sql). Restituisce {id: errore} per
    i source che non eseguono. L'esecuzione e' completa (count su tutto il
    risultato) perche' alcuni errori, come le conversioni di tipo, emergono
    solo leggendo le righe e non in fase di binding.
    """
    failing = {}
    with read_connection(path, timeout=timeout) as con:
        if con is None:
            return {mid: "il database del sito non contiene ancora tabelle"
                    for mid, _ in sources}
        for mid, sql in sources:
            try:
                con.execute(f"SELECT count(*) FROM ({sql}) AS hdn_check").fetchone()
            except duckdb.Error as exc:
                failing[mid] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
    return failing
