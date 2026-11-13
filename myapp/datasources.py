"""Sorgenti dati dell'endpoint (menu "Data source").

- CSV: caricati nel DuckDB locale dalla home, come sempre.
- SQLite e DuckDB: file caricati dall'amministratore e conservati in
  HDN_STATE_DIR/sources/.
- MySQL, PostgreSQL, Oracle, MS SQL e Aurora (MySQL o PostgreSQL compatibile):
  credenziali salvate in HDN_STATE_DIR/datasources.json con permessi 0600. Non
  tornano mai alle pagine; i messaggi d'errore non contengono la password.
- GraphDB (triple store): il database e' gia' un grafo RDF, quindi Ontop e il
  mapping non servono. Dopo il controllo di privacy la query SPARQL va a
  GraphDB (myapp/triplestore.py).

La sorgente attiva guida Run Relational Queries, il mapping e Ontop, oppure,
per un triple store, il solo dispatch delle query SPARQL.
"""
import json
import os
import sqlite3
import tempfile
from collections import OrderedDict, namedtuple
from pathlib import Path

from django.conf import settings

Kind = namedtuple("Kind", "label icon kind fields default_port")
Field = namedtuple("Field", "name label secret default placeholder required", defaults=(True,))

REMOTE_FIELDS = [
    Field("host", "Host", False, "", "db.example.org"),
    Field("port", "Port", False, "", "", False),
    Field("database", "Database", False, "", ""),
    Field("user", "User", False, "", ""),
    Field("password", "Password", True, "", ""),
]

# GraphDB: le credenziali servono solo se la sicurezza del server e' attiva
GRAPHDB_FIELDS = [
    Field("url", "Server URL", False, "", "http://graphdb:7200"),
    Field("repository", "Repository", False, "", "hero"),
    Field("user", "User (optional)", False, "", "", False),
    Field("password", "Password (optional)", True, "", "", False),
]

KINDS = OrderedDict([
    ("csv", Kind("CSV files", None, "csv", [], None)),
    ("duckdb", Kind("DuckDB file", "duckdb", "file", [], None)),
    ("sqlite", Kind("SQLite file", "sqlite", "file", [], None)),
    ("mysql", Kind("MySQL", "mysql", "remote", REMOTE_FIELDS, 3306)),
    ("postgresql", Kind("PostgreSQL", "postgresql", "remote", REMOTE_FIELDS, 5432)),
    ("oracle", Kind("Oracle", "oracle", "remote", REMOTE_FIELDS, 1521)),
    ("mssql", Kind("MS SQL Server", "mssql", "remote", REMOTE_FIELDS, 1433)),
    ("aurora", Kind("Amazon Aurora", "aurora", "remote", REMOTE_FIELDS, 3306)),
    ("graphdb", Kind("GraphDB", "graphdb", "triplestore", GRAPHDB_FIELDS, 7200)),
])

# Sezioni della pagina Data source
GROUPS = OrderedDict([
    ("files", ("Files", ("csv", "file"))),
    ("rdbms", ("RDBMS", ("remote",))),
    ("triplestore", ("Triple store", ("triplestore",))),
])

AURORA_ENGINES = {"mysql": 3306, "postgresql": 5432}
REDACTED = "***"
TIMEOUT = 10


class DataSourceError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _not_a_database(kind):
    if KINDS[kind].kind == "triplestore":
        return DataSourceError("not-a-database", "a triple store has no tables: query it with SPARQL")
    return DataSourceError("not-a-database", "CSV files are loaded into the local DuckDB")


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
    for f in KINDS[kind].fields:
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
    for f in KINDS[kind].fields:
        if not f.required:
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
    out = {f.name: current.get(f.name, "") for f in KINDS[kind].fields if not f.secret}
    out["has_password"] = bool(current.get("password"))
    out["engine"] = current.get("engine", "mysql")
    return out


def is_triplestore(kind):
    return KINDS[kind].kind == "triplestore"


def active_is_triplestore():
    """True se la sorgente attiva e' un triple store: le query SPARQL non passano da Ontop."""
    return is_triplestore(active())


def configured(kind):
    if KINDS[kind].kind in ("remote", "triplestore"):
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


LIST_TABLES_SQL = {
    "mysql": ("SELECT table_name FROM information_schema.tables "
              "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name"),
    "postgresql": ("SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' "
                   "AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_name"),
    "oracle": "SELECT table_name FROM user_tables ORDER BY table_name",
    "mssql": ("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
              "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"),
}


def _engine(kind):
    params = stored(kind)
    return params.get("engine", "mysql") if kind == "aurora" else kind


def _connect(kind):
    """Connessione in sola lettura al database remoto configurato. Ritorna (motore, connessione)."""
    if missing(kind, None):
        raise DataSourceError("not-configured", "fill in the connection details first")
    p = stored(kind)
    engine = _engine(kind)
    port = _port(kind, p)
    try:
        if engine == "mysql":
            import pymysql
            con = pymysql.connect(host=p["host"], port=port, user=p["user"], password=p["password"],
                                  database=p["database"], connect_timeout=TIMEOUT, autocommit=True)
            with con.cursor() as cur:
                # il mapping usa identificatori tra doppi apici, come DuckDB
                cur.execute("SET SESSION sql_mode = 'ANSI_QUOTES'")
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        elif engine == "postgresql":
            import psycopg
            con = psycopg.connect(host=p["host"], port=port, user=p["user"], password=p["password"],
                                  dbname=p["database"], connect_timeout=TIMEOUT, autocommit=True,
                                  options="-c default_transaction_read_only=on")
        elif engine == "oracle":
            import oracledb
            con = oracledb.connect(user=p["user"], password=p["password"],
                                   dsn=f"{p['host']}:{port}/{p['database']}",
                                   tcp_connect_timeout=TIMEOUT)
        elif engine == "mssql":
            import pymssql
            con = pymssql.connect(server=p["host"], port=str(port), user=p["user"],
                                  password=p["password"], database=p["database"],
                                  login_timeout=TIMEOUT)
        else:
            raise DataSourceError("unknown-engine", engine)
    except DataSourceError:
        raise
    except ImportError as exc:
        raise DataSourceError("driver-missing", f"Python driver not installed ({exc})")
    except Exception as exc:
        raise DataSourceError("unreachable", _scrub(exc, p.get("password", "")))
    return engine, con


def list_tables(kind):
    """Nomi delle tabelle della sorgente configurata; DataSourceError se non raggiungibile."""
    k = KINDS[kind]
    if k.kind == "file":
        return _list_file_tables(kind)
    if k.kind != "remote":
        raise _not_a_database(kind)
    engine, con = _connect(kind)
    try:
        cur = con.cursor()
        cur.execute(LIST_TABLES_SQL[engine])
        return [r[0] for r in cur.fetchall()]
    except Exception as exc:
        raise DataSourceError("query-error", _scrub(exc, stored(kind).get("password", "")))
    finally:
        con.close()


MAX_ROWS = 5000


def _result(cursor):
    if cursor.description is None:
        return [], [], False
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchmany(MAX_ROWS + 1)
    return cols, [list(r) for r in rows[:MAX_ROWS]], len(rows) > MAX_ROWS


def run_query(kind, sql):
    """Esegue SQL sulla sorgente in sola lettura. Ritorna (colonne, righe, troncato).

    Per MySQL e PostgreSQL la sessione e' read-only; per Oracle e MS SQL vale
    solo il privilegio dell'utente: usare un utente di sola lettura.
    """
    k = KINDS[kind]
    if k.kind == "file":
        return _run_file(kind, sql)
    if k.kind != "remote":
        raise _not_a_database(kind)
    _engine_name, con = _connect(kind)
    try:
        cur = con.cursor()
        cur.execute(sql)
        return _result(cur)
    except Exception as exc:
        raise DataSourceError("query-error", _scrub(exc, stored(kind).get("password", "")))
    finally:
        con.close()


def _run_file(kind, sql):
    path = file_path(kind)
    if not path.exists():
        raise DataSourceError("not-configured", "no file uploaded yet")
    try:
        if kind == "sqlite":
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                return _result(con.execute(sql))
            finally:
                con.close()
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
        try:
            return _result(con.execute(sql))
        finally:
            con.close()
    except DataSourceError:
        raise
    except Exception as exc:
        raise DataSourceError("query-error", _scrub(exc, ""))


def active():
    """Sorgente attiva: "csv" (DuckDB locale) o una chiave di KINDS."""
    value = _read_registry().get("active", "csv")
    return value if value in KINDS else "csv"


def set_active(kind):
    if kind != "csv" and not configured(kind):
        raise DataSourceError("not-configured", "configure the source before using it")
    data = _read_registry()
    data["active"] = kind
    _write_registry(data)


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


SQLITE_MAGIC = b"SQLite format 3\x00"


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


# ---------------------------------------------------------------------------
# Catalogo, tipi e validazione per il sito attivo
# ---------------------------------------------------------------------------

SCHEMA_SQL = {
    "mysql": ("SELECT table_name, column_name, data_type FROM information_schema.columns "
              "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position"),
    "postgresql": ("SELECT table_name, column_name, data_type FROM information_schema.columns "
                   "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                   "ORDER BY table_name, ordinal_position"),
    "oracle": ("SELECT table_name, column_name, data_type FROM user_tab_columns "
               "ORDER BY table_name, column_id"),
    "mssql": ("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
              "ORDER BY TABLE_NAME, ORDINAL_POSITION"),
}


def normalize_type(raw):
    """Tipo del motore in un nome DuckDB, cosi' che il resto del codice lo riconosca."""
    t = (raw or "").upper()
    if not t or any(x in t for x in ("CHAR", "TEXT", "CLOB", "STRING")):
        return "VARCHAR"
    if "INT" in t or "SERIAL" in t:
        return "BIGINT"
    if any(x in t for x in ("DECIMAL", "NUMERIC", "NUMBER", "FLOAT", "DOUBLE", "REAL", "MONEY")):
        return "DOUBLE"
    if "DATE" in t and "TIME" not in t:
        return "DATE"
    if "TIME" in t:
        return "TIMESTAMP"
    if "BOOL" in t or "BIT" in t:
        return "BOOLEAN"
    return t


def schema(kind):
    """{tabella: [(colonna, tipo normalizzato)]} della sorgente attiva o configurata."""
    k = KINDS[kind]
    if k.kind == "file":
        return _file_schema(kind)
    if k.kind != "remote":
        raise _not_a_database(kind)
    engine, con = _connect(kind)
    try:
        cur = con.cursor()
        cur.execute(SCHEMA_SQL[engine])
        out = {}
        for table, column, dtype in cur.fetchall():
            out.setdefault(table, []).append((column, normalize_type(dtype)))
        return out
    except Exception as exc:
        raise DataSourceError("query-error", _scrub(exc, stored(kind).get("password", "")))
    finally:
        con.close()


def _file_schema(kind):
    path = file_path(kind)
    if not path.exists():
        raise DataSourceError("not-configured", "no file uploaded yet")
    if kind == "sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            out = {}
            for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
                cols = con.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")')
                out[table] = [(c[1], normalize_type(c[2])) for c in cols.fetchall()]
            return out
        finally:
            con.close()
    import duckdb
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                           "WHERE table_schema = 'main' ORDER BY table_name, ordinal_position").fetchall()
        out = {}
        for table, column, dtype in rows:
            out.setdefault(table, []).append((column, normalize_type(dtype)))
        return out
    finally:
        con.close()


def tables_columns(kind):
    """{tabella: [colonne]} della sorgente."""
    return {t: [c for c, _ in cols] for t, cols in schema(kind).items()}


def column_types(kind):
    """{tabella: {colonna: tipo normalizzato}} della sorgente."""
    return {t: dict(cols) for t, cols in schema(kind).items()}


def failing_sources(kind, sources):
    """Esegue ogni query sorgente sulla sorgente; {id: errore} per quelle che non eseguono.

    Come per DuckDB, l'esecuzione conta tutte le righe: alcuni errori di tipo
    emergono solo leggendo il risultato.
    """
    failing = {}
    k = KINDS[kind]
    if k.kind == "file":
        import duckdb
        for mid, sql in sources:
            try:
                if kind == "sqlite":
                    con = sqlite3.connect(f"file:{file_path(kind)}?mode=ro", uri=True)
                    try:
                        con.execute(f"SELECT count(*) FROM ({sql}) hdn_check").fetchone()
                    finally:
                        con.close()
                else:
                    con = duckdb.connect(str(file_path(kind)), read_only=True)
                    try:
                        con.execute(f"SELECT count(*) FROM ({sql}) hdn_check").fetchone()
                    finally:
                        con.close()
            except Exception as exc:
                failing[mid] = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        return failing
    _engine_name, con = _connect(kind)
    try:
        cur = con.cursor()
        for mid, sql in sources:
            try:
                cur.execute(f"SELECT count(*) FROM ({sql}) hdn_check")
                cur.fetchall()
            except Exception as exc:
                failing[mid] = _scrub(exc, stored(kind).get("password", "")).splitlines()[0]
    finally:
        con.close()
    return failing


def sqlite_nullable_keys(kind="sqlite"):
    """Colonne chiave (PRIMARY KEY) senza NOT NULL nel file SQLite: Ontop le rifiuta."""
    path = file_path(kind)
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        out = []
        for t in tables:
            for _cid, name, _type, notnull, _dflt, pk in con.execute(
                    f'PRAGMA table_info("{t.replace(chr(34), chr(34) * 2)}")'):
                if pk and not notnull:
                    out.append(f"{t}.{name}")
        return out
    finally:
        con.close()
