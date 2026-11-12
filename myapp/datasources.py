"""Sorgenti dati dell'endpoint (menu "Data source").

- CSV: caricati nel DuckDB locale dalla home, come sempre.
- SQLite e DuckDB: file caricati dall'amministratore e conservati in
  HDN_STATE_DIR/sources/.
- MySQL, PostgreSQL, Oracle, MS SQL e Aurora (MySQL o PostgreSQL compatibile):
  credenziali salvate in HDN_STATE_DIR/datasources.json con permessi 0600. Non
  tornano mai alle pagine; i messaggi d'errore non contengono la password.

Questa fase configura e verifica le sorgenti. Ontop, mapping e pagina SQL
continuano a usare il DuckDB locale: l'attivazione di una sorgente remota e'
la fase successiva.
"""
import json
import os
import sqlite3
import tempfile
from collections import OrderedDict, namedtuple
from pathlib import Path

from django.conf import settings

Kind = namedtuple("Kind", "label icon kind fields default_port")
Field = namedtuple("Field", "name label secret default placeholder")

REMOTE_FIELDS = [
    Field("host", "Host", False, "", "db.example.org"),
    Field("port", "Port", False, "", ""),
    Field("database", "Database", False, "", ""),
    Field("user", "User", False, "", ""),
    Field("password", "Password", True, "", ""),
]

KINDS = OrderedDict([
    ("csv", Kind("CSV files", None, "csv", [], None)),
    ("sqlite", Kind("SQLite file", "sqlite", "file", [], None)),
    ("duckdb", Kind("DuckDB file", "duckdb", "file", [], None)),
    ("mysql", Kind("MySQL", "mysql", "remote", REMOTE_FIELDS, 3306)),
    ("postgresql", Kind("PostgreSQL", "postgresql", "remote", REMOTE_FIELDS, 5432)),
    ("oracle", Kind("Oracle", "oracle", "remote", REMOTE_FIELDS, 1521)),
    ("mssql", Kind("MS SQL Server", "mssql", "remote", REMOTE_FIELDS, 1433)),
    ("aurora", Kind("Amazon Aurora", "aurora", "remote", REMOTE_FIELDS, 3306)),
])

AURORA_ENGINES = {"mysql": 3306, "postgresql": 5432}
REDACTED = "***"
TIMEOUT = 10


class DataSourceError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _state_dir():
    return Path(settings.HDN_STATE_DIR)


def _registry_path():
    return _state_dir() / "datasources.json"


def file_path(kind):
    return _state_dir() / "sources" / f"{kind}.db"


def _read_registry():
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _write_registry(data):
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stored(kind):
    """Parametri salvati, password inclusa: uso interno, mai nelle pagine."""
    return _read_registry().get(kind, {})


def save_remote(kind, values):
    """Salva i parametri; una password lasciata vuota conserva quella attuale."""
    current = stored(kind)
    params = {}
    for f in REMOTE_FIELDS:
        value = (values.get(f.name) or "").strip()
        if not value and f.secret:
            value = current.get(f.name, "")
        params[f.name] = value
    if kind == "aurora":
        engine = (values.get("engine") or "mysql").strip()
        params["engine"] = engine if engine in AURORA_ENGINES else "mysql"
    data = _read_registry()
    data[kind] = params
    _write_registry(data)


def missing(kind, values):
    """Campi obbligatori mancanti (valori nuovi, con la password salvata come ripiego)."""
    current = stored(kind)
    out = []
    for f in REMOTE_FIELDS:
        if f.name == "port":
            continue
        value = (values.get(f.name) or "").strip() if values is not None else current.get(f.name, "")
        if f.secret and not value:
            value = current.get(f.name, "")
        if not value:
            out.append(f.label)
    return out


def public(kind):
    """Parametri per le pagine: la password diventa il flag has_password."""
    current = stored(kind)
    out = {f.name: current.get(f.name, "") for f in REMOTE_FIELDS if not f.secret}
    out["has_password"] = bool(current.get("password"))
    out["engine"] = current.get("engine", "mysql")
    return out


def configured(kind):
    if KINDS[kind].kind == "remote":
        return not missing(kind, None)
    if KINDS[kind].kind == "file":
        return file_path(kind).exists()
    return True


def _port(kind, params):
    raw = params.get("port") or ""
    if raw:
        try:
            return int(raw)
        except ValueError:
            raise DataSourceError("invalid-port", "the port must be a number")
    if kind == "aurora":
        return AURORA_ENGINES.get(params.get("engine", "mysql"), 3306)
    return KINDS[kind].default_port


def _scrub(exc, secret):
    text = str(exc)
    if secret:
        text = text.replace(secret, REDACTED)
    return f"{type(exc).__name__}: {text[:200]}"


def list_tables(kind):
    """Nomi delle tabelle della sorgente configurata; DataSourceError se non raggiungibile."""
    k = KINDS[kind]
    if k.kind == "file":
        return _list_file_tables(kind)
    if k.kind != "remote":
        raise DataSourceError("not-a-database", "CSV files are loaded into the local DuckDB")
    params = stored(kind)
    if missing(kind, None):
        raise DataSourceError("not-configured", "fill in the connection details first")
    engine = params.get("engine", "mysql") if kind == "aurora" else kind
    port = _port(kind, params)
    try:
        if engine == "mysql":
            return _list_mysql(params, port)
        if engine == "postgresql":
            return _list_postgres(params, port)
        if engine == "oracle":
            return _list_oracle(params, port)
        if engine == "mssql":
            return _list_mssql(params, port)
    except DataSourceError:
        raise
    except ImportError as exc:
        raise DataSourceError("driver-missing", f"Python driver not installed ({exc})")
    except Exception as exc:
        raise DataSourceError("unreachable", _scrub(exc, params.get("password", "")))
    raise DataSourceError("unknown-engine", engine)


def _list_mysql(p, port):
    import pymysql
    con = pymysql.connect(host=p["host"], port=port, user=p["user"], password=p["password"],
                          database=p["database"], connect_timeout=TIMEOUT)
    try:
        with con.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = %s AND table_type = 'BASE TABLE' ORDER BY table_name",
                        (p["database"],))
            return [r[0] for r in cur.fetchall()]
    finally:
        con.close()


def _list_postgres(p, port):
    import psycopg
    con = psycopg.connect(host=p["host"], port=port, user=p["user"], password=p["password"],
                          dbname=p["database"], connect_timeout=TIMEOUT,
                          options="-c default_transaction_read_only=on")
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def _list_oracle(p, port):
    import oracledb
    con = oracledb.connect(user=p["user"], password=p["password"],
                           dsn=f"{p['host']}:{port}/{p['database']}", tcp_connect_timeout=TIMEOUT)
    try:
        cur = con.cursor()
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        return [r[0] for r in cur.fetchall()]
    finally:
        con.close()


def _list_mssql(p, port):
    import pymssql
    con = pymssql.connect(server=p["host"], port=str(port), user=p["user"],
                          password=p["password"], database=p["database"], login_timeout=TIMEOUT)
    try:
        cur = con.cursor()
        cur.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME")
        return [r[0] for r in cur.fetchall()]
    finally:
        con.close()


SQLITE_MAGIC = b"SQLite format 3\x00"


def _list_file_tables(kind):
    path = file_path(kind)
    if not path.exists():
        raise DataSourceError("not-configured", "no file uploaded yet")
    if kind == "sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                               "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
            return [r[0] for r in rows]
        finally:
            con.close()
    import duckdb
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute("SELECT table_name FROM information_schema.tables "
                           "WHERE table_schema = 'main' ORDER BY table_name").fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def check_file_header(kind, head):
    """Il file caricato deve avere la firma del formato dichiarato."""
    if kind == "sqlite":
        return head.startswith(SQLITE_MAGIC)
    if kind == "duckdb":
        return len(head) >= 12 and head[8:12] == b"DUCK"
    return False


def store_file(kind, chunks):
    """Scrive il file caricato nella cartella della sorgente, sostituendo il precedente."""
    target = file_path(kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f"{kind}.")
    try:
        with os.fdopen(fd, "wb") as out:
            for chunk in chunks:
                out.write(chunk)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return target
