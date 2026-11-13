"""Segnaposto mappati a una combinazione di colonne: somma o concatenazione.

Nel mapping un segnaposto e' una colonna. Per una combinazione la regola legge
da una tabella derivata che espone la combinazione come colonna virtuale:

    FROM (SELECT "c1", "c2", ..., CONCAT(...) AS hdn_nome FROM "T") AS "T"

Il resto della catena (rinomina del target e del source, isnan, validazione)
tratta ``hdn_nome`` come una colonna qualunque. L'alias non e' fra apici: cosi'
si riduce al caso del motore come i riferimenti nudi della query esterna.

Dal file ``.obda`` non si ricostruiscono modalita', colonne e separatore, che
la UI deve mostrare di nuovo. Stanno in un file accanto al mapping, scritto a
ogni salvataggio, e alla rilettura vale solo cio' che il mapping conferma
(stessa tabella, stesso alias): un file non piu' aggiornato viene ignorato.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from . import dialects

SUM = "sum"
CONCAT = "concat"
MODES = (SUM, CONCAT)
MAX_COLUMNS = 20
MAX_SEPARATOR = 20
VIRTUAL_PREFIX = "hdn_"

_NUMERIC_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "INT", "BIGINT", "HUGEINT", "MEDIUMINT",
    "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
    "INT1", "INT2", "INT4", "INT8", "FLOAT", "FLOAT4", "FLOAT8", "REAL",
    "DOUBLE", "DOUBLE PRECISION", "DECIMAL", "NUMERIC", "NUMBER", "MONEY",
}


class CombinationError(ValueError):
    pass


def is_combination(value):
    return isinstance(value, dict)


def is_numeric_type(sql_type):
    """True per un tipo che si puo' sommare (interi, decimali, virgola mobile)."""
    base = (sql_type or "").upper().split("(")[0].strip()
    return base in _NUMERIC_TYPES


def _check_separator(separator):
    if not isinstance(separator, str) or len(separator) > MAX_SEPARATOR:
        raise CombinationError(f"the separator can have at most {MAX_SEPARATOR} characters")
    # il source sta su una riga e Ontop raddoppia i backslash nei literal
    if any(ord(ch) < 32 or ch == "\\" for ch in separator):
        raise CombinationError("the separator cannot contain backslashes or control characters")
    return separator


def parse(value, columns, types=None):
    """Combinazione valida ``{"mode", "columns", "separator"}``, o CombinationError.

    ``columns`` sono le colonne della tabella; ``types`` ({colonna: tipo}) serve
    a controllare che una somma abbia solo colonne numeriche, e si puo' omettere.
    """
    if not isinstance(value, dict):
        raise CombinationError("not a combination")
    mode = value.get("mode")
    if mode not in MODES:
        raise CombinationError(f"unknown mode {mode!r}")
    cols = value.get("columns")
    if not isinstance(cols, list) or not 2 <= len(cols) <= MAX_COLUMNS:
        raise CombinationError(f"a combination needs 2 to {MAX_COLUMNS} columns")
    if len(set(cols)) != len(cols):
        raise CombinationError("a column cannot be used twice")
    for c in cols:
        if not isinstance(c, str) or c not in columns:
            raise CombinationError(f"unknown column {c!r}")
    if mode == SUM and types is not None:
        bad = [c for c in cols if not is_numeric_type(types.get(c))]
        if bad:
            raise CombinationError("a sum needs numeric columns, not " + ", ".join(bad))
    separator = _check_separator(value.get("separator", "")) if mode == CONCAT else ""
    return {"mode": mode, "columns": list(cols), "separator": separator}


def virtual_name(var, taken):
    """Nome della colonna virtuale: hdn_<segnaposto>, distinto da quelli in ``taken`` (minuscoli)."""
    name = VIRTUAL_PREFIX + var
    while name.lower() in taken:
        name += "_"
    return name


def expression(dialect, combo):
    if combo["mode"] == SUM:
        return dialects.sum_expression(dialect, combo["columns"])
    return dialects.concat_expression(dialect, combo["columns"], combo.get("separator", ""))


def result_type(combo):
    """Tipo logico della colonna virtuale, come lo vede adapt_isnan."""
    return "DOUBLE" if combo["mode"] == SUM else "VARCHAR"


def wrap_source(source, table, columns, virtuals):
    """Sostituisce ``FROM "tabella"`` con la tabella derivata che aggiunge le colonne virtuali.

    ``virtuals`` e' una lista di (nome, espressione). La tabella derivata espone
    tutte le colonne della tabella piu' le virtuali, con lo stesso alias della
    tabella: la query esterna non cambia.
    """
    if not virtuals:
        return source
    items = [dialects.quote(c) for c in columns] + [f"{expr} AS {name}" for name, expr in virtuals]
    quoted = dialects.quote(table)
    inner = f"SELECT {', '.join(items)} FROM {quoted}"
    pattern = re.compile(r"\bFROM\s+" + re.escape(quoted))
    return pattern.sub(lambda _m: f"FROM ({inner}) AS {quoted}", source)


# ---------------------------------------------------------------------------
# Metadati accanto al mapping
# ---------------------------------------------------------------------------

def sidecar_path(obda_file):
    return Path(obda_file).with_suffix(".combinations.json")


def load_sidecar(obda_file):
    """{mappingId: {segnaposto: voce}} oppure {} se manca o non e' leggibile."""
    try:
        data = json.loads(sidecar_path(obda_file).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_sidecar(obda_file, data):
    """Scrive i metadati in modo atomico; senza combinazioni toglie il file."""
    path = sidecar_path(obda_file)
    if not data:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def restored(entry, saved_column, table, source_line, dialect=dialects.DUCKDB):
    """Combinazione per la UI se il mapping salvato la conferma, altrimenti None.

    ``saved_column`` e' il nome che il target del mapping salvato ha al posto del
    segnaposto. La voce vale solo se e' la sua colonna virtuale, se la tabella e'
    la stessa e se il source contiene proprio l'espressione che quella
    combinazione genera: cosi' la UI mostra cio' che il file calcola davvero,
    anche dopo un ripristino manuale di una versione precedente del mapping.
    """
    if not isinstance(entry, dict) or entry.get("virtual") != saved_column:
        return None
    if entry.get("table") != table:
        return None
    mode, cols = entry.get("mode"), entry.get("columns")
    if mode not in MODES or not isinstance(cols, list) or len(cols) < 2:
        return None
    if not all(isinstance(c, str) for c in cols):
        return None
    combo = {"mode": mode, "columns": list(cols),
             "separator": entry.get("separator", "") if mode == CONCAT else ""}
    try:
        expected = expression(dialect, combo) + " AS " + saved_column
    except ValueError:
        return None
    if expected.lower() not in source_line.lower():
        return None
    return combo
