"""Relational schema of the site, as the LLM sees it.

Adapted from mbg_bootstrap/sql_schema.py (Schema, Table, Column and
``to_focused_text``). The schema is read from the site DuckDB database
instead of a DDL file; uploaded CSVs carry no foreign keys.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class Column:
    name: str
    sql_type: str = ""


@dataclass
class Table:
    name: str
    columns: List[Column] = field(default_factory=list)


@dataclass
class Schema:
    tables: Dict[str, Table] = field(default_factory=dict)

    @classmethod
    def from_site(cls, tables_columns, column_types=None):
        column_types = column_types or {}
        s = cls()
        for t, cols in tables_columns.items():
            types = column_types.get(t, {})
            s.tables[t] = Table(t, [Column(c, str(types.get(c, "") or "")) for c in cols])
        return s

    def table(self, name):
        """Table by name, case-insensitively, or None."""
        if name in self.tables:
            return self.tables[name]
        low = (name or "").strip().strip('"').lower()
        return next((t for n, t in self.tables.items() if n.lower() == low), None)

    def signature(self):
        return sorted((t.name, tuple(c.name for c in t.columns)) for t in self.tables.values())

    def to_focused_text(self, focus_cols: List[str], *, top_k_tables: int = 15,
                        max_cols_focused: int = 500, max_cols_other: int = 30) -> str:
        """Full columns (with types) for the tables most relevant to ``focus_cols``."""
        if not self.tables:
            return "(no tables)"
        focus_lc = {c.lower() for c in (focus_cols or []) if c}
        scored: List[Tuple[int, str]] = []
        for tname, t in self.tables.items():
            hits = len({c.name.lower() for c in t.columns} & focus_lc) if focus_lc else 0
            scored.append((hits, tname))
        scored.sort(key=lambda x: (-x[0], x[1].lower()))
        # With few tables (the usual case for a site) every table is shown in full.
        focus_tables = ({t for h, t in scored[:top_k_tables] if h > 0}
                        if len(self.tables) > top_k_tables else set(self.tables))
        lines: List[str] = []
        for tname in sorted(self.tables, key=str.lower):
            t = self.tables[tname]
            cols = t.columns
            if tname in focus_tables:
                shown = cols[:max_cols_focused]
                txt = ", ".join(f"{c.name}:{c.sql_type}" if c.sql_type else c.name for c in shown)
                if len(cols) > max_cols_focused:
                    txt += f" (+{len(cols) - max_cols_focused} more)"
            else:
                txt = ", ".join(c.name for c in cols[:max_cols_other])
                if len(cols) > max_cols_other:
                    txt += f" (+{len(cols) - max_cols_other} more)"
            lines.append(f'- "{t.name}"({txt})')
        return "\n".join(lines)
