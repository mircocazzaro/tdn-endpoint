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
