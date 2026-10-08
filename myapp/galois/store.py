"""Stato, viste e aggiornamento delle tabelle della modalita' Galois.

File in HDN_STATE_DIR/galois/:
- config.json      modalita' attiva, tabelle e colonne, iterazioni
- azure.json       endpoint, deployment, versione API e chiave (permessi 0600)
- galois.duckdb    solo viste: CREATE VIEW t AS SELECT * FROM read_parquet(...)
- data/<t>.parquet righe della tabella t, riscritte a ogni aggiornamento
- galois.obda      mapping fisso generato dallo schema
- ontop.properties connessione di Ontop a galois.duckdb, in sola lettura
- status.json      ultimo aggiornamento di ogni tabella

Ontop tiene galois.duckdb aperto in sola lettura. Le viste si ricreano solo
quando cambia la configurazione, con Ontop fermo; i dati invece cambiano
riscrivendo il Parquet con os.replace, che DuckDB rilegge a ogni query: un
aggiornamento non richiede di fermare Ontop.
"""
import fcntl
import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import duckdb
from django.conf import settings

from . import schema
from .llm import AzureOpenAI, LLMError
from .scan import TableScan

audit = logging.getLogger("hdn.audit")

DEFAULT_MAX_ITER = 3
MAX_ITER = 6
MAX_ROWS = 2000


class GaloisError(Exception):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# --- percorsi e file di stato -------------------------------------------------

def base_dir():
    return Path(settings.HDN_STATE_DIR) / "galois"


def mapping_path():
    return base_dir() / "galois.obda"


def properties_path():
    return base_dir() / "ontop.properties"


def database_path():
    return base_dir() / "galois.duckdb"


def parquet_path(table):
    return base_dir() / "data" / f"{table}.parquet"


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_config():
    cfg = _read_json(base_dir() / "config.json", {})
    tables = schema.default_config()
    for name, t in (cfg.get("tables") or {}).items():
        if name in tables and isinstance(t, dict):
            cols = [c for c in t.get("columns", [])
                    if any(c == x.name for x in schema.BY_NAME[name].columns)]
            tables[name] = {"enabled": bool(t.get("enabled", True)), "columns": cols}
    try:
        max_iter = min(max(int(cfg.get("max_iter", DEFAULT_MAX_ITER)), 1), MAX_ITER)
    except (TypeError, ValueError):
        max_iter = DEFAULT_MAX_ITER
    return {"enabled": bool(cfg.get("enabled", False)), "tables": tables, "max_iter": max_iter}


def save_config(cfg):
    _write_json(base_dir() / "config.json", cfg)


def enabled():
    return load_config()["enabled"]


def load_azure():
    return _read_json(base_dir() / "azure.json", {})


def save_azure(endpoint, deployment, api_version, api_key=None):
    """Salva la configurazione Azure; ``api_key`` None conserva quella esistente."""
    current = load_azure()
    data = {"endpoint": endpoint.strip(), "deployment": deployment.strip(),
            "api_version": (api_version or "").strip() or None,
            "api_key": api_key.strip() if api_key else current.get("api_key", "")}
    _write_json(base_dir() / "azure.json", data, mode=0o600)


def llm_client():
    a = load_azure()
    try:
        return AzureOpenAI(a.get("endpoint"), a.get("deployment"), a.get("api_key"),
                           a.get("api_version"))
    except LLMError as exc:
        raise GaloisError(exc.code, exc.detail)


def status():
    return _read_json(base_dir() / "status.json", {})


def _set_status(table, **values):
    path = base_dir() / "status.json"
    with _lock("status"):
        s = _read_json(path, {})
        s[table] = {**s.get(table, {}), **values}
        _write_json(path, s)


@contextmanager
def _lock(name):
    path = base_dir() / "locks" / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# --- schema attivo, mapping e viste -----------------------------------------

def active_tables(cfg=None):
    """``[(Table, [colonne])]`` delle tabelle attive."""
    cfg = cfg or load_config()
    return [(t, schema.active_columns(t, cfg["tables"][t.name]))
            for t in schema.TABLES if cfg["tables"][t.name]["enabled"]]


def build_mapping(cfg=None, declared=None):
    """``(testo_obda, colonne_escluse)``.

    Una colonna (o una tabella intera, se manca la sua classe) resta fuori dal
    mapping se usa un termine che l'ontologia attiva non dichiara piu'.
    """
    from .. import ontology
    if declared is None:
        g = ontology.parse(ontology.active_path().read_text(encoding="utf-8"))
        declared = ontology.declared_terms(g) | ontology.offline_import_terms(g)
    spaces = {ontology.namespace(t) for t in declared} - ontology.STANDARD_NAMESPACES
    blocks, excluded = [], []
    for table, cols in active_tables(cfg):
        tb = schema.mapping_blocks(table, cols)
        for i, (mid, target, source) in enumerate(tb):
            missing = [t for t in ontology.target_terms(target, schema.PREFIXES)
                       if t not in declared and ontology.namespace(t) in spaces]
            if missing:
                excluded.append((mid, missing))
                if i == 0:
                    break
                continue
            blocks.append((mid, target, source))
    return schema.obda_text(blocks), excluded


def _write_parquet(path, columns, rows):
    """Scrive ``rows`` (tutte stringhe o None) nel Parquet ``path``, in modo atomico."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".parquet")
    os.close(fd)
    os.unlink(tmp)
    con = duckdb.connect()
    try:
        cols = ", ".join(f'"{c}" VARCHAR' for c in columns)
        con.execute(f"CREATE TABLE t ({cols})")
        if rows:
            ph = ", ".join("?" for _ in columns)
            con.executemany(f"INSERT INTO t VALUES ({ph})", [[r.get(c) for c in columns] for r in rows])
        con.execute(f"COPY t TO '{tmp}' (FORMAT parquet)")
    finally:
        con.close()
    os.replace(tmp, path)


def _parquet_columns(path):
    con = duckdb.connect()
    try:
        return [r[0] for r in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]
    except duckdb.Error:
        return None
    finally:
        con.close()


def apply(cfg=None):
    """Allinea viste, Parquet, mapping e properties alla configurazione.

    Da chiamare con Ontop fermo (tiene galois.duckdb aperto). Restituisce le
    colonne escluse dal mapping.
    """
    cfg = cfg or load_config()
    tables = active_tables(cfg)
    for table, cols in tables:
        p = parquet_path(table.name)
        if _parquet_columns(p) != cols:
            # Colonne cambiate: tabella vuota, si ripopola alla prima richiesta.
            _write_parquet(p, cols, [])
            _set_status(table.name, rows=0, refreshed=None, error=None)
    con = duckdb.connect(str(database_path()))
    try:
        existing = {r[0] for r in con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal").fetchall()}
        for name in existing - {t.name for t, _ in tables}:
            con.execute(f'DROP VIEW "{name}"')
        for table, _ in tables:
            path = str(parquet_path(table.name).resolve()).replace("'", "''")
            con.execute(f"CREATE OR REPLACE VIEW \"{table.name}\" AS "
                        f"SELECT * FROM read_parquet('{path}')")
    finally:
        con.close()
    text, excluded = build_mapping(cfg)
    mapping_path().write_text(text, encoding="utf-8")
    properties_path().write_text(
        f"jdbc.url = jdbc:duckdb:{database_path().resolve()}\n"
        "jdbc.driver = org.duckdb.DuckDBDriver\n"
        "jdbc.property.duckdb.read_only = true\n", encoding="utf-8")
    return excluded


# --- aggiornamento ----------------------------------------------------------

def clean_rows(table, columns, raw_rows):
    """Righe valide: chiave obbligatoria e nel formato atteso, una riga per chiave."""
    out, seen = [], set()
    for raw in raw_rows:
        row = {}
        for name in columns:
            v = raw.get(name)
            if v is None or isinstance(v, (list, dict)) or str(v).strip() == "":
                row[name] = None
                continue
            fn = table.key_clean if name == table.key else table.column(name).clean
            row[name] = fn(v)
        k = row[table.key]
        if k is None or k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def refresh_table(name, llm=None, not_before=None, cfg=None):
    """Ripopola la tabella ``name`` dall'LLM; restituisce il dizionario di stato.

    ``not_before``: se un altro processo ha aggiornato la tabella dopo questo
    istante (mentre si aspettava il lock), l'aggiornamento non si ripete.
    """
    cfg = cfg or load_config()
    table = schema.BY_NAME[name]
    cols = schema.active_columns(table, cfg["tables"][name])
    with _lock(name):
        if not_before is not None:
            st = status().get(name, {})
            if st.get("refreshed") and st["refreshed"] >= not_before and not st.get("error"):
                return st
        llm = llm or llm_client()
        meta = {"name": table.name, "columns": [{"name": c, "dtype": "string"} for c in cols]}
        t0 = time.time()
        try:
            res = TableScan(llm, meta, cols, max_iter=cfg["max_iter"], max_rows=MAX_ROWS).run()
        except LLMError as exc:
            _set_status(name, error=exc.code, error_at=time.time())
            raise GaloisError(exc.code, exc.detail)
        rows = clean_rows(table, cols, res.rows)
        _write_parquet(parquet_path(name), cols, rows)
        st = dict(rows=len(rows), raw_rows=len(res.rows), tokens=res.tokens, calls=res.calls,
                  seconds=round(time.time() - t0, 2), refreshed=time.time(), error=None)
        _set_status(name, **st)
    audit.info("galois-refresh table=%s rows=%d raw=%d tokens=%d calls=%d",
               name, st["rows"], st["raw_rows"], st["tokens"], st["calls"])
    return st


def tables_for_query(sparql, query_prefixes, cfg=None):
    """Tabelle da ripopolare per una query, ricavate dal mapping fisso.

    Una tabella serve se la query cita un termine che solo il suo mapping usa
    (la sua classe o una sua proprieta'). Se la query cita anche termini
    comuni a piu' tabelle (es. rdfs:label), servono anche le tabelle
    referenziate dalle chiavi esterne delle tabelle trovate, perche' quei
    termini potrebbero riferirsi a loro. Se la query non cita alcun termine
    proprio di una tabella (es. i template su bto:Patient), nessuna tabella
    puo' contribuire alla risposta e non si interroga l'LLM.
    """
    from .. import ontology
    active = active_tables(cfg)
    terms_by_table = {}
    for table, cols in active:
        ts = set()
        for _, target, _ in schema.mapping_blocks(table, cols):
            ts |= ontology.target_terms(target, schema.PREFIXES)
        terms_by_table[table.name] = ts
    count = {}
    for ts in terms_by_table.values():
        for t in ts:
            count[t] = count.get(t, 0) + 1
    q_terms = ontology.target_terms(sparql, dict(query_prefixes))
    needed = {n for n, ts in terms_by_table.items() if any(count[t] == 1 for t in ts & q_terms)}
    if not needed:
        return []
    if any(count.get(t, 0) > 1 for t in q_terms):
        refs = {c.references for t, cols in active if t.name in needed
                for c in t.columns if c.name in cols and c.references}
        needed |= {r for r in refs if r in terms_by_table}
    return [t.name for t, _ in active if t.name in needed]


def refresh_for_query(sparql, query_prefixes):
    """Ripopola, in parallelo, le tabelle che servono alla query. GaloisError se fallisce."""
    started = time.time()
    cfg = load_config()
    names = tables_for_query(sparql, query_prefixes, cfg)
    if not names:
        return []
    llm = llm_client()
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = [pool.submit(refresh_table, n, llm, started, cfg) for n in names]
        for f in futures:
            f.result()
    return names
