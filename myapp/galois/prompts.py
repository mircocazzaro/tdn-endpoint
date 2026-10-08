"""Prompt della TableScan di Galois.

Vendorizzato da py-galois (https://github.com/mircocazzaro/galois_repro_resources,
commit 00e84bd, pygalois/py_galois/prompts.py). Testo dei prompt invariato;
sono state tolte le funzioni non usate qui (KeyScan, classificazione dei
predicati, confidenza).
"""
import json
from typing import Dict, List, Optional


def json_schema_for_table(table: Dict) -> Dict:
    """Build a minimal JSON schema for a list of tuples (array of objects)."""
    props = {}
    required = []
    for col in table.get("columns", []):
        name = col["name"]
        dtype = col.get("dtype", "string").lower()
        if dtype in ("int", "integer", "bigint"):
            t = "integer"
        elif dtype in ("float", "double", "real", "numeric", "decimal"):
            t = "number"
        elif dtype in ("boolean", "bool"):
            t = "boolean"
        else:
            t = "string"
        props[name] = {"type": [t, "string"] if t != "string" else ["string"]}
        required.append(name)
    return {"type": "array", "items": {"type": "object", "properties": props, "required": required}}


def table_scan_first_prompt(table_name: str, attrs: List[str], json_schema: Dict,
                            cond: Optional[str] = None) -> str:
    attrs_str = ", ".join(attrs)
    schema_str = json.dumps(json_schema, ensure_ascii=False)
    where = f" where {cond}" if cond else ""
    return (
        f"Given the following query, populate the table with actual values. "
        f"query: select {attrs_str} from {table_name}{where}. "
        f"Respond with JSON only. Don’t add any comment. "
        f"Use the following JSON schema: {schema_str}."
    )


def table_scan_iter_prompt() -> str:
    return "List more values if there are more, otherwise return an empty JSON. Respond with JSON only."
