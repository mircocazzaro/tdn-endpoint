"""Diagramma mermaid (erDiagram) dello schema DuckDB mostrato nella home.

I nomi di tabella e colonna vengono dai CSV caricati e possono contenere spazi,
trattini, parentesi o apici, che la grammatica erDiagram non accetta: il
diagramma non veniva disegnato e la pagina non segnalava nulla.

Ogni tabella diventa un'entita' con un identificatore sintetico (``t0``,
``t1``, ...) e il nome reale come etichetta; ogni colonna diventa un attributo
con un nome ridotto ai caratteri ammessi. Verificato con il parser di mermaid
11.4.1, la versione caricata dalla pagina.
"""

import re

_INVALID = re.compile(r"[^A-Za-z0-9_]")


def _attribute(name, used):
    base = _INVALID.sub("_", name).strip("_") or "col"
    if not base[0].isalpha():
        base = "c_" + base
    candidate, n = base, 2
    while candidate.lower() in used:
        candidate, n = f"{base}_{n}", n + 1
    used.add(candidate.lower())
    return candidate


def _label(name):
    # Le etichette sono fra doppi apici: un doppio apice nel nome la chiuderebbe.
    return name.replace('"', "'").replace("\n", " ")


def er_diagram(tables_columns):
    """Testo erDiagram per ``{tabella: [colonne]}``; stringa vuota senza tabelle."""
    if not tables_columns:
        return ""
    lines = ["erDiagram"]
    for i, (table, columns) in enumerate(tables_columns.items()):
        lines.append(f'  t{i}["{_label(table)}"] {{')
        used = set()
        for col in columns:
            lines.append(f"    string {_attribute(col, used)}")
        lines.append("  }")
    return "\n".join(lines) + "\n"
