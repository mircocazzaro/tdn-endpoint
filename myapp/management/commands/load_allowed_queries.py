import re, hashlib
from pathlib import Path
import duckdb
from django.core.management.base import BaseCommand
from django.conf import settings

CATALOG = Path(__file__).resolve().parent.parent / "queries_catalog.md"
DB_PATH = settings.MEDIA_ROOT + "/allowed_queries.duckdb"

class Command(BaseCommand):
    help = "Load & hash the SPARQL catalog into DuckDB"

    def handle(self, *args, **opts):
        text = CATALOG.read_text(encoding="utf-8")

        # find all “## Level N” sections
        sections = re.split(r"^##\s*Level\s*(\d+)\s*$", text, flags=re.M)[1:]
        # sections = [level, body, level, body, ...]
        it = iter(sections)

        entries = []
        for level, body in zip(it, it):
            lvl = int(level)
            # find each ```sparql ... ```
            for m in re.finditer(r"```sparql\s*(.+?)```", body, re.S):
                query = m.group(1).strip()
                h = hashlib.sha512(query.encode("utf-8")).hexdigest()
                entries.append((h, lvl, query))

        # write into DuckDB
        con = duckdb.connect(DB_PATH)
        con.execute("""
          CREATE TABLE IF NOT EXISTS allowed_queries (
            hash TEXT PRIMARY KEY,
            level INTEGER,
            query TEXT
          );
        """)
        # upsert all
        for h, lvl, q in entries:
            con.execute("""
              INSERT OR REPLACE INTO allowed_queries VALUES (?, ?, ?)
            """, [h, lvl, q])
        con.close()
        self.stdout.write(self.style.SUCCESS(f"Loaded {len(entries)} queries"))