"""Prompt of the mapping bootstrap.

Adapted from mbg_bootstrap/prompts_source.py (``build_source_prediction_prompt``):
same task (skeleton fixed, predict only the relational source), same output
discipline (one JSON object, one item per requested index, null when unsure,
never invent tables or columns). Differences for HDN:

- a rule binds the template's placeholders to columns of ONE local table,
  because that is what Map Data to HERO can save; no free SQL is written;
- each rule shows the template designer's SQL as a structural hint (it is
  written against the reference schema, not the site's);
- instead of a sample of the whole ontology, the prompt describes only the
  ontology terms the rules in the chunk use, with label, comment, domain
  and range.
"""
import json
from dataclasses import dataclass

SYSTEM = (
    "You are an OBDA (Ontology-Based Data Access) expert specialising in Ontop mappings. "
    "You are given mapping rules from a template: the ontology side (classes, predicates, "
    "IRI templates) is fixed and was written for a reference database. Your task is ONLY to "
    "ground each rule in the LOCAL relational schema: choose the local table that holds the "
    "data of the rule and bind every placeholder to a column of that table. "
    "Do NOT change classes, predicates or IRI templates. Output ONLY valid JSON as described."
)

JSON_DESC = """\
Return ONLY valid JSON (no markdown, no comments, no extra text).
The JSON MUST be an object with exactly one key:

  {"items": [ ... ]}

where each item grounds one rule:

  {
    "index"    : <integer>  (same index as the rule),
    "table"    : "<local table name>" | null,
    "bindings" : [ {"placeholder": "<placeholder>", "column": "<column of that table>" | null}, ... ]
  }

HARD RULES:
1) Use JSON null (without quotes). Never output the string "null" or "none".
2) "table" MUST be a table of the LOCAL schema, and every column MUST be a column
   of that table, written exactly as in the schema. Do NOT invent tables or columns.
3) bindings must contain one entry per placeholder listed for the rule, in the same order.
4) If no local table holds the data of a rule, set "table" to null and every column to null.
5) If you cannot determine a column, set it to null: a wrong binding is worse than none.
6) The identifier placeholder of the subject (e.g. {patient}) must be bound to the column
   that identifies the entity in the chosen table.
7) Output items for ALL requested indexes.
"""


@dataclass
class PromptBundle:
    system: str
    user: str


def rule_text(index, block):
    return "\n".join([
        f"[{index}] mappingId={block['mappingId']}",
        f"    target: {' '.join(block['target'].split())}",
        f"    template_sql (reference schema, structural hint only): {' '.join(block['source_tpl'].split())}",
        f"    placeholders_to_bind: {', '.join(block['placeholders'])}",
        "",
    ])


def response_format():
    """Azure structured output schema (used when the deployment supports it)."""
    return {"type": "json_schema", "json_schema": {"name": "mapping_grounding", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["index", "table", "bindings"],
            "properties": {
                "index": {"type": "integer"},
                "table": {"type": ["string", "null"]},
                "bindings": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["placeholder", "column"],
                    "properties": {"placeholder": {"type": "string"},
                                   "column": {"type": ["string", "null"]}}}}}}}}}}}


def build(schema, ontology_text, indexed_blocks, total):
    focus = []
    for _, b in indexed_blocks:
        for p in b["placeholders"]:
            if p.lower() not in {f.lower() for f in focus}:
                focus.append(p)
    start, end = indexed_blocks[0][0], indexed_blocks[-1][0]
    user = f"""TASK
----
For each mapping rule in the range [{start}..{end}] (out of {total} total), choose the LOCAL
table holding its data and bind each placeholder to a column of that table.

ONTOLOGY TERMS USED BY THESE RULES
----------------------------------
{ontology_text}

LOCAL RELATIONAL SCHEMA (tables and columns available; types after ':')
-----------------------------------------------------------------------
(Placeholders of this chunk: {", ".join(focus) if focus else "(none)"})

{schema.to_focused_text(focus)}

MAPPING RULES (ontology side fixed; table and columns TO PREDICT)
-----------------------------------------------------------------
{"".join(rule_text(i, b) for i, b in indexed_blocks)}
INSTRUCTIONS
------------
- Match each rule to the local table whose content corresponds to the rule's class and
  predicates; column names and types are the main evidence.
- Use table and column names EXACTLY as they appear in the local schema (case-sensitive).
- Placeholders used only in the template_sql filters (e.g. a column tested for NULL) must be
  bound to the local column with the same meaning.
- If unsure, use null rather than guessing.

{JSON_DESC}
"""
    return PromptBundle(SYSTEM, user)


def parse_json(text):
    """JSON from the model output (mbg_bootstrap.llm_azure._extract_json_fallback)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t)
    except ValueError:
        pass
    for a, b in (("{", "}"), ("[", "]")):
        i, j = t.find(a), t.rfind(b)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except ValueError:
                continue
    raise ValueError("no JSON in the model output")
