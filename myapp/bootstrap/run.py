"""Run the mapping bootstrap and keep its suggestions.

Pipeline (after mbg_bootstrap, source-prediction task):

1. the rules are the blocks of the mapping template, with the placeholders
   Map Data to HERO asks to bind (``views.template_mapping_blocks``);
2. rules are sent in chunks; a chunk whose answer is truncated is split in
   two and retried (as the mbg_bootstrap drivers do on TruncatedOutputError);
3. the answer is parsed and validated against the local schema: unknown
   tables, columns of another table and invented names are dropped;
4. deterministic snap (mbg_bootstrap ``snap_obvious_columns``): a placeholder
   left unbound is bound to the column of the chosen table with the same
   name, case-insensitively;
5. the suggestions are stored in HDN_STATE_DIR/bootstrap/suggestions.json
   with the schema and template they were computed for: they are offered only
   while both are unchanged.
"""
import hashlib
import json
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings

from .. import ontology
from ..galois.llm import LLMError
from . import prompt as P

audit = logging.getLogger("hdn.audit")

CHUNK = 8
MAX_TOKENS = 6000
MAX_WORKERS = 4


class BootstrapError(Exception):
    pass


# --- ontology context -------------------------------------------------------

def ontology_text(blocks, header):
    """Label, comment, kind, domain and range of the terms the blocks use."""
    import rdflib
    from rdflib.namespace import OWL, RDF, RDFS

    g = ontology.parse(ontology.active_path().read_text(encoding="utf-8"))
    prefixes = ontology.mapping_prefixes(header)
    terms = []
    for b in blocks:
        for t in sorted(ontology.target_terms(b["target"], prefixes)):
            if t not in terms:
                terms.append(t)
    kinds = {OWL.Class: "class", RDFS.Class: "class", OWL.ObjectProperty: "object property",
             OWL.DatatypeProperty: "data property", OWL.AnnotationProperty: "annotation property"}

    def local(x):
        s = str(x)
        return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    lines = []
    for t in terms:
        u = rdflib.URIRef(t)
        kind = next((k for c, k in kinds.items() if (u, RDF.type, c) in g), None)
        if kind is None:
            continue
        label = next((str(x) for x in g.objects(u, RDFS.label)), "")
        comment = " ".join(next((str(x) for x in g.objects(u, RDFS.comment)), "").split())[:200]
        dom = ", ".join(sorted(local(x) for x in g.objects(u, RDFS.domain) if isinstance(x, rdflib.URIRef)))
        rng = ", ".join(sorted(local(x) for x in g.objects(u, RDFS.range) if isinstance(x, rdflib.URIRef)))
        extra = "; ".join(x for x in (f"domain: {dom}" if dom else "", f"range: {rng}" if rng else "") if x)
        lines.append(f"- {local(u)} ({kind})" + (f' "{label}"' if label else "")
                     + (f" [{extra}]" if extra else "") + (f": {comment}" if comment else ""))
    return "\n".join(lines) or "(no ontology terms)"


# --- validation ---------------------------------------------------------------

def validate(item, block, schema):
    """``(table, pairs, notes)`` for one parsed item; invalid parts dropped."""
    notes = []
    table = schema.table(item.get("table")) if item.get("table") else None
    if item.get("table") and table is None:
        notes.append(f"unknown table {item.get('table')!r}")
    if table is None:
        return None, {}, notes
    cols = {c.name.lower(): c.name for c in table.columns}
    pairs = {}
    raw = item.get("bindings") or []
    if isinstance(raw, dict):   # tolerate {"placeholder": "column"}
        raw = [{"placeholder": k, "column": v} for k, v in raw.items()]
    for b in raw:
        if not isinstance(b, dict):
            continue
        ph, col = b.get("placeholder"), b.get("column")
        if ph not in block["placeholders"] or not isinstance(col, str) or col.strip().lower() in ("", "null", "none"):
            continue
        real = cols.get(col.strip().strip('"').lower())
        if real is None:
            notes.append(f"{ph}: column {col!r} is not in {table.name!r}")
            continue
        pairs[ph] = real
    # snap: placeholder with the same name as a column of the chosen table
    for ph in block["placeholders"]:
        if ph not in pairs and ph.lower() in cols:
            pairs[ph] = cols[ph.lower()]
    return table.name, pairs, notes


# --- LLM calls ----------------------------------------------------------------

def _call(llm, bundle):
    messages = [{"role": "system", "content": bundle.system + "\n\nReturn ONLY valid JSON. No markdown."},
                {"role": "user", "content": bundle.user}]
    try:
        r = llm.chat(messages, max_tokens=MAX_TOKENS, response_format=P.response_format())
    except LLMError as exc:
        if exc.code != "http-error":
            raise
        # deployment without structured outputs: plain JSON instructions only
        r = llm.chat(messages, max_tokens=MAX_TOKENS)
    return r


def _chunk(llm, schema, onto_text, indexed, total, depth=0):
    """Items for ``indexed`` [(index, block)], splitting on truncation."""
    bundle = P.build(schema, onto_text, indexed, total)
    r = _call(llm, bundle)
    tokens = r.usage_tokens or 0
    if r.finish_reason == "length":
        if len(indexed) == 1 or depth > 4:
            return [], tokens, [f"{indexed[0][1]['mappingId']}: answer truncated"]
        half = len(indexed) // 2
        a = _chunk(llm, schema, onto_text, indexed[:half], total, depth + 1)
        b = _chunk(llm, schema, onto_text, indexed[half:], total, depth + 1)
        return a[0] + b[0], tokens + a[1] + b[1], a[2] + b[2]
    try:
        payload = P.parse_json(r.text)
    except ValueError:
        return [], tokens, [f"rules {indexed[0][0]}..{indexed[-1][0]}: the answer is not JSON"]
    items = payload.get("items") if isinstance(payload, dict) else payload
    return [i for i in (items or []) if isinstance(i, dict)], tokens, []


def bootstrap(llm, header, blocks, schema):
    """``{mappingId: {"table", "pairs", "notes"}}`` and a summary dict."""
    if not schema.tables:
        raise BootstrapError("no local table: upload the CSV files first")
    t0 = time.time()
    onto = ontology_text(blocks, header)
    indexed = list(enumerate(blocks, start=1))
    chunks = [indexed[i:i + CHUNK] for i in range(0, len(indexed), CHUNK)]
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(chunks) or 1)) as pool:
        results = list(pool.map(lambda c: _chunk(llm, schema, onto, c, len(blocks)), chunks))
    by_index, tokens, problems = {}, 0, []
    for items, tok, probs in results:
        tokens += tok
        problems += probs
        for it in items:
            try:
                by_index[int(it.get("index"))] = it
            except (TypeError, ValueError):
                continue
    suggestions = {}
    for i, b in indexed:
        table, pairs, notes = validate(by_index.get(i, {}), b, schema)
        if table and pairs:
            suggestions[b["mappingId"]] = {"table": table, "pairs": pairs, "notes": notes,
                                           "complete": len(pairs) == len(b["placeholders"])}
        elif notes:
            problems += [f"{b['mappingId']}: {n}" for n in notes]
    summary = {"rules": len(blocks), "suggested": len(suggestions),
               "complete": sum(s["complete"] for s in suggestions.values()),
               "tokens": tokens, "seconds": round(time.time() - t0, 1), "problems": problems[:50]}
    audit.info("mapping-bootstrap rules=%d suggested=%d complete=%d tokens=%d",
               summary["rules"], summary["suggested"], summary["complete"], tokens)
    return suggestions, summary


# --- stored suggestions -------------------------------------------------------

def _path():
    return Path(settings.HDN_STATE_DIR) / "bootstrap" / "suggestions.json"


def fingerprint(header, blocks, schema):
    raw = json.dumps([header, [(b["mappingId"], b["target"], b["placeholders"]) for b in blocks],
                      schema.signature()], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def save(fp, suggestions, summary):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"fingerprint": fp, "created": time.time(), "suggestions": suggestions,
                   "summary": summary}, fh)
    os.replace(tmp, p)


def load(fp):
    """Stored suggestions if computed for the same template and schema, else None."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return data if data.get("fingerprint") == fp else None


def discard():
    try:
        _path().unlink()
    except FileNotFoundError:
        pass
