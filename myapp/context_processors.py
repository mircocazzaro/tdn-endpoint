# myapp/context_processors.py
import os
import duckdb
from django.conf import settings

def level_choices(request):
    # ensure the file / table exists
    levels = ["L0 - Boolean Queries", "L1 - Simple COUNT Aggregations", "L2 - Full Aggregations (AVG, ecc.)", "L3 - Grouped Data", "L4 - Limited Access to Non‐Sensitive Data", "L5 - Access to Individual Patient Data", "L6 - Full Access to Data"]
    lvl = 'L0 - Boolean Queries'
    db = settings.LEVEL_DB
    conn = duckdb.connect(db)
    # create table if needed
    conn.execute("""
       CREATE TABLE IF NOT EXISTS options (
         key TEXT PRIMARY KEY,
         value TEXT
       )
    """)
    # try to read
    try:
        rows = conn.execute(
            "SELECT value FROM options WHERE key='level'"
        ).fetchone()
        if rows and rows[0] in levels:
            lvl = rows[0]
    except Exception:
        pass
    conn.close()

    return {
        'current_level': lvl,
        #'level_options': [f"L{i}" for i in range(7)],
        'level_options': levels,
    }
