"""Differenze SQL tra i motori delle sorgenti dati.

Il template di mapping e' scritto per DuckDB: usa isnan() e il test di testo
numerico di DuckDB. Gli altri motori non hanno isnan(), quindi lo stesso
significato e' espresso qui per ciascun dialetto. Per DuckDB le espressioni
restano quelle di sempre.

Approssimazioni note: su MS SQL e SQLite il test di testo numerico e' un
filtro sui caratteri (non una regex completa); su Oracle, MySQL, PostgreSQL e
DuckDB e' una regex.
"""

DUCKDB = "duckdb"
SQLITE = "sqlite"
MYSQL = "mysql"
POSTGRESQL = "postgresql"
ORACLE = "oracle"
MSSQL = "mssql"

DUCKDB_NUMERIC_PATTERN = r"'[+-]?[0-9]+(\.[0-9]+)?'"
# Regex dei dialetti: [.] invece di \. evita gli escape, che Ontop raddoppia nei literal
_MYSQL_NUMBER = r"^[+-]?[0-9]+([.][0-9]+)?$"  # classe [.]: niente backslash, che Ontop raddoppia
_PG_NUMBER = r"^[+-]?[0-9]+([.][0-9]+)?$"


def numeric_text(dialect, col):
    """Espressione booleana: il testo di ``col`` e' un numero (senza spazi)."""
    if dialect == DUCKDB:
        return f"regexp_full_match(CAST({col} AS VARCHAR), {DUCKDB_NUMERIC_PATTERN})"
    if dialect == MYSQL:
        return f"CAST({col} AS CHAR) REGEXP '{_MYSQL_NUMBER}'"
    if dialect == POSTGRESQL:
        return f"CAST({col} AS TEXT) ~ '{_PG_NUMBER}'"
    if dialect == ORACLE:
        return f"REGEXP_LIKE(TO_CHAR({col}), '{_PG_NUMBER}')"
    if dialect == MSSQL:
        return f"(ISNUMERIC({col}) = 1 AND {col} NOT LIKE '%[^0-9.+-]%')"
    if dialect == SQLITE:
        # glob(pattern, valore) invece di "NOT GLOB": il parser di Ontop rifiuta NOT GLOB
        return (f"(typeof({col}) IN ('integer', 'real') OR (typeof({col}) = 'text' "
                f"AND glob('*[^0-9.+-]*', {col}) = 0 AND glob('*[0-9]*', {col}) = 1))")
    raise ValueError(f"unknown dialect {dialect}")


def numeric_cast(dialect, col):
    """Cast di ``col`` a numero (da chiamare solo dopo il test numeric_text)."""
    casts = {
        DUCKDB: f"CAST({col} AS DOUBLE)",
        MYSQL: f"CAST({col} AS DOUBLE)",
        POSTGRESQL: f"CAST({col} AS DOUBLE PRECISION)",
        ORACLE: f"TO_NUMBER({col})",
        MSSQL: f"CAST({col} AS FLOAT)",
        SQLITE: f"CAST({col} AS REAL)",
    }
    if dialect not in casts:
        raise ValueError(f"unknown dialect {dialect}")
    return casts[dialect]


# ---------------------------------------------------------------------------
# Colonne combinate (somma o concatenazione) in un segnaposto
# ---------------------------------------------------------------------------

def quote(name):
    """Identificatore fra doppi apici (il mapping usa gia' "tabella" cosi'; su MySQL
    la sessione ha ANSI_QUOTES)."""
    return '"' + name.replace('"', '""') + '"'


def text_cast(dialect, expr):
    """Cast di ``expr`` a testo, con il tipo che ogni motore accetta."""
    casts = {
        DUCKDB: f"CAST({expr} AS VARCHAR)",
        MYSQL: f"CAST({expr} AS CHAR)",
        POSTGRESQL: f"CAST({expr} AS TEXT)",
        SQLITE: f"CAST({expr} AS TEXT)",
        ORACLE: f"CAST({expr} AS VARCHAR2(4000))",
        MSSQL: f"CAST({expr} AS NVARCHAR(4000))",
    }
    if dialect not in casts:
        raise ValueError(f"unknown dialect {dialect}")
    return casts[dialect]


def sum_expression(dialect, columns):
    """Somma aritmetica delle colonne. Come in SQL, un NULL rende NULL il risultato."""
    if dialect not in (DUCKDB, SQLITE, MYSQL, POSTGRESQL, ORACLE, MSSQL):
        raise ValueError(f"unknown dialect {dialect}")
    return "(" + " + ".join(quote(c) for c in columns) + ")"


def concat_expression(dialect, columns, separator=""):
    """Concatenazione delle colonne, nell'ordine dato, con un separatore opzionale.

    Un valore NULL conta come testo vuoto su ogni motore (COALESCE): CONCAT non
    e' uniforme, in MySQL un NULL annulla tutto il risultato. Il separatore e'
    un literal: il chiamante ha gia' escluso backslash e caratteri di controllo.
    """
    if dialect not in (DUCKDB, SQLITE, MYSQL, POSTGRESQL, ORACLE, MSSQL):
        raise ValueError(f"unknown dialect {dialect}")
    sep = "'" + separator.replace("'", "''") + "'"
    items = []
    for i, c in enumerate(columns):
        if i and separator:
            items.append(sep)
        items.append(f"COALESCE({text_cast(dialect, quote(c))}, '')")
    # CONCAT a due argomenti, annidato: Oracle ne accetta solo due e il dialetto SQLite
    # di Ontop li impone (CONCAT2). La forma binaria vale su ogni motore.
    expr = items[0]
    for item in items[1:]:
        expr = f"CONCAT({expr}, {item})"
    return expr
