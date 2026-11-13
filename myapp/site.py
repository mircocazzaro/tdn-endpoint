"""Sito dati attivo: il database su cui lavorano mapping, colonne e Ontop.

Con la sorgente locale (CSV nel DuckDB del sito) tutto resta come prima. Con
una sorgente remota o un file SQLite/DuckDB, catalogo, validazione delle
sorgenti e proprieta' di Ontop vengono da quella sorgente.

Il mapping generato porta con se' il dialetto del sito in cui e' stato salvato
(HDN_STATE_DIR/ontop/mapping_dialect): cambiare sorgente richiede di salvarlo
di nuovo, altrimenti Ontop risponde con errori SQL.
"""
import os
import tempfile
from pathlib import Path

from django.conf import settings

from . import datasources as ds
from . import datastore
from .dialects import DUCKDB, SQLITE

LOCAL = "csv"


def active():
    return ds.active()


def is_local():
    return ds.active() == LOCAL


def dialect():
    k = ds.active()
    if k in (LOCAL, "duckdb"):
        return DUCKDB
    if k == "sqlite":
        return SQLITE
    return ds._engine(k)


def tables_columns(local_path):
    if is_local():
        return datastore.tables_columns(local_path)
    return ds.tables_columns(ds.active())


def column_types(local_path):
    if is_local():
        return datastore.column_types(local_path)
    return ds.column_types(ds.active())


def failing_sources(local_path, sources):
    if is_local():
        return datastore.failing_sources(local_path, sources)
    return ds.failing_sources(ds.active(), sources)


# ---------------------------------------------------------------------------
# Dialetto del mapping salvato
# ---------------------------------------------------------------------------

def _dialect_file():
    return Path(settings.HDN_STATE_DIR) / "ontop" / "mapping_dialect"


def record_mapping_dialect():
    """Da chiamare quando il mapping viene salvato: registra il dialetto del sito."""
    p = _dialect_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dialect(), encoding="utf-8")


def mapping_dialect():
    """Dialetto con cui e' stato generato il mapping attivo (DuckDB se non registrato)."""
    try:
        return _dialect_file().read_text(encoding="utf-8").strip() or DUCKDB
    except FileNotFoundError:
        return DUCKDB


def mapping_matches_site():
    return mapping_dialect() == dialect()


# ---------------------------------------------------------------------------
# Proprieta' di Ontop per le sorgenti remote e per i file
# ---------------------------------------------------------------------------

def _escape(value):
    """Escape per il formato .properties di Java (':' '=' e '\\' compresi)."""
    out = []
    for ch in str(value):
        if ch in "\\:=#! ":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def ontop_properties_text(kind=None):
    """Proprieta' di Ontop per la sorgente (default: quella attiva)."""
    kind = kind or ds.active()
    if kind == LOCAL:
        raise ValueError("the local DuckDB uses the repository properties file")
    if ds.is_triplestore(kind):
        raise ValueError("a triple store is queried directly: Ontop is not used")
    k = ds.KINDS[kind]
    if k.kind == "file":
        path = ds.file_path(kind)
        if kind == "sqlite":
            lines = [f"jdbc.url = jdbc:sqlite:{path}", "jdbc.driver = org.sqlite.JDBC",
                     "jdbc.property.open_mode = 1"]
        else:
            lines = [f"jdbc.url = jdbc:duckdb:{path}", "jdbc.driver = org.duckdb.DuckDBDriver",
                     "jdbc.property.duckdb.read_only = true"]
        return "\n".join(_prop(*line.split(" = ", 1)) for line in lines) + "\n"

    p = ds.stored(kind)
    engine = ds._engine(kind)
    port = ds._port(kind, p)
    host, database = p.get("host", ""), p.get("database", "")
    if engine == "mysql":
        url = f"jdbc:mysql://{host}:{port}/{database}?sessionVariables=sql_mode='ANSI_QUOTES'"
        driver = "com.mysql.cj.jdbc.Driver"
    elif engine == "postgresql":
        url = f"jdbc:postgresql://{host}:{port}/{database}"
        driver = "org.postgresql.Driver"
    elif engine == "oracle":
        url = f"jdbc:oracle:thin:@//{host}:{port}/{database}"
        driver = "oracle.jdbc.OracleDriver"
    elif engine == "mssql":
        url = (f"jdbc:sqlserver://{host}:{port};databaseName={database};"
               "encrypt=true;trustServerCertificate=false")
        driver = "com.microsoft.sqlserver.jdbc.SQLServerDriver"
    else:
        raise ValueError(f"unknown engine {engine}")
    lines = [_prop("jdbc.url", url), _prop("jdbc.driver", driver),
             _prop("jdbc.user", p.get("user", "")), _prop("jdbc.password", p.get("password", ""))]
    return "\n".join(lines) + "\n"


def _prop(key, value):
    return f"{key} = {_escape(value)}"


def ontop_properties_path():
    return Path(settings.HDN_STATE_DIR) / "ontop" / "site.properties"


def write_ontop_properties():
    """Scrive il file delle proprieta' per la sorgente attiva (0600: contiene la password)."""
    target = ontop_properties_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(ontop_properties_text())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return target
