"""Riparazione e normalizzazione dell'output JSON dell'LLM.

Vendorizzato da py-galois (commit 00e84bd, pygalois/py_galois/json_utils.py),
invariato nel comportamento.
"""
import json
import re
from typing import Any, Dict, List


def try_parse_json(text: str) -> Any:
    """Attempt to parse the LLM output as JSON. Try a few simple repairs heuristics."""
    text = (text or "").strip()
    text = re.sub(r'^```(json)?', '', text).strip()
    text = re.sub(r'```$', '', text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\[.*\]', text, flags=re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    if text and not (text.startswith('[') and text.endswith(']')):
        try:
            return json.loads(f"[{text}]")
        except Exception:
            pass
    text2 = re.sub(r',\s*([\}\]])', r'\1', text)
    try:
        return json.loads(text2)
    except Exception:
        return None


def normalize_row(row: Dict[str, Any], columns: List[str]) -> Dict[str, Any]:
    """Keep only known columns, drop extras."""
    return {c: row.get(c, None) for c in columns}


def ensure_list_of_dicts(obj: Any) -> List[Dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    return []
