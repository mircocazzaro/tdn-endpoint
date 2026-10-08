"""TableScan di Galois: popola una relazione interrogando l'LLM.

Vendorizzato da py-galois (commit 00e84bd, pygalois/py_galois/scan.py,
classe TableScan). Stesso algoritmo: primo prompt con la SELECT della
tabella, poi richieste "List more values" finche' l'LLM non aggiunge righe
nuove o si raggiunge ``max_iter``. Differenze:
- niente print di prompt e risposte (andrebbero nei log del server);
- limite ``max_rows`` alle righe accumulate;
- restituisce anche il numero di chiamate.
"""
import json
from typing import Any, Dict, List, Optional

from .json_utils import ensure_list_of_dicts, normalize_row, try_parse_json
from .prompts import json_schema_for_table, table_scan_first_prompt, table_scan_iter_prompt


class ScanResult:
    def __init__(self, rows, tokens, latency, calls):
        self.rows = rows
        self.tokens = tokens
        self.latency = latency
        self.calls = calls


def _messages(context, prompt):
    return [{"role": r, "content": c} for r, c in context] + [{"role": "user", "content": prompt}]


def _hashable(v: Any) -> Any:
    return json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v


class TableScan:
    def __init__(self, llm, table_meta: Dict, select_attrs: List[str],
                 pushed_cond: Optional[str] = None, max_iter: int = 6, max_rows: int = 2000):
        self.llm = llm
        self.table_meta = table_meta
        self.select_attrs = select_attrs
        self.pushed_cond = pushed_cond
        self.max_iter = max_iter
        self.max_rows = max_rows

    def run(self) -> ScanResult:
        schema = json_schema_for_table(self.table_meta)
        first = table_scan_first_prompt(self.table_meta["name"], self.select_attrs, schema,
                                        self.pushed_cond)
        context: List = []
        seen, rows = set(), []
        tokens, latency, calls = 0, 0.0, 0

        def absorb(data):
            new = 0
            for obj in ensure_list_of_dicts(data):
                if len(rows) >= self.max_rows:
                    break
                row = normalize_row(obj, self.select_attrs)
                key = tuple(_hashable(row.get(c)) for c in self.select_attrs)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
                    new += 1
            return new

        resp = self.llm.chat(_messages(context, first))
        calls += 1
        tokens += resp.usage_tokens
        latency += resp.latency_s
        data = try_parse_json(resp.text)
        absorb(data)

        it = 1
        while it < self.max_iter and len(rows) < self.max_rows:
            context.extend([("user", first), ("assistant", json.dumps(ensure_list_of_dicts(data)))])
            resp = self.llm.chat(_messages(context, table_scan_iter_prompt()))
            calls += 1
            tokens += resp.usage_tokens
            latency += resp.latency_s
            data = try_parse_json(resp.text)
            if absorb(data) == 0:
                break
            it += 1
        return ScanResult(rows, tokens, latency, calls)
