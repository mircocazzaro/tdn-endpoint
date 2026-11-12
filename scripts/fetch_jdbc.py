"""Scarica i driver JDBC che Ontop usa per le sorgenti remote e per SQLite.

Uso: python scripts/fetch_jdbc.py <cartella>   (es. myapp/obda/jdbc)

Ogni jar e' fissato per versione e SHA-256: un file diverso dal previsto viene
scartato. I jar sono quelli pubblicati su Maven Central (verificati anche
contro i loro SHA-1 pubblicati). Oracle JDBC e' distribuito da Oracle con
licenza propria: verificarla prima di redistribuire l'immagine.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

MAVEN = "https://repo1.maven.org/maven2"
DRIVERS = {
    "mysql-connector-j-8.4.0.jar": (
        f"{MAVEN}/com/mysql/mysql-connector-j/8.4.0/mysql-connector-j-8.4.0.jar",
        "d77962877d010777cff997015da90ee689f0f4bb76848340e1488f2b83332af5"),
    "postgresql-42.7.4.jar": (
        f"{MAVEN}/org/postgresql/postgresql/42.7.4/postgresql-42.7.4.jar",
        "188976721ead8e8627eb6d8389d500dccc0c9bebd885268a3047180274a6031e"),
    "sqlite-jdbc-3.46.1.3.jar": (
        f"{MAVEN}/org/xerial/sqlite-jdbc/3.46.1.3/sqlite-jdbc-3.46.1.3.jar",
        "4a4832720a65eaf7f4d6fd7ede52087b994dc5633c076f9e994dc0c8b4b0b4fa"),
    "mssql-jdbc-12.8.1.jre11.jar": (
        f"{MAVEN}/com/microsoft/sqlserver/mssql-jdbc/12.8.1.jre11/mssql-jdbc-12.8.1.jre11.jar",
        "e6933c0711e598a224060e52ed31392f720a4a7664e85d8ae37c52a85b67ebb0"),
    "ojdbc11-23.5.0.24.07.jar": (
        f"{MAVEN}/com/oracle/database/jdbc/ojdbc11/23.5.0.24.07/ojdbc11-23.5.0.24.07.jar",
        "2cc69b821da842c5f5be8877444631cf9fa89561f1f6f5ad7bf1293eb669fc27"),
}


def fetch(target_dir):
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for name, (url, sha256) in DRIVERS.items():
        dest = target_dir / name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256:
            continue
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise SystemExit(f"checksum mismatch for {name}")
        dest.write_bytes(data)
        print(f"fetched {name}")


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else "myapp/obda/jdbc")
