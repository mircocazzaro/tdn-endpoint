"""LLM bootstrap of the site mapping.

Adapted from mbg_bootstrap (mapping bootstrapping with LLMs), source-
prediction task: the ontology side of each mapping rule is fixed by the
mapping template (class, predicates, IRI templates, the template designer's
SQL), and the LLM predicts only the relational source on the local schema,
i.e. the table and the column bound to each placeholder.

The proposal is never saved directly: it pre-fills *Map Data to HERO*, where
the administrator reviews it and saves through the usual validation (every
source query is executed on the local data before the mapping is written).

Only metadata reach the LLM: table and column names, column types, the
template and the ontology terms it uses. No data values are sent.

- ``schema``: relational schema rendering (from mbg_bootstrap.sql_schema);
- ``prompt``: prompt and JSON contract (from mbg_bootstrap.prompts_source);
- ``run``: chunked LLM calls, parsing, validation and deterministic snaps
  (from mbg_bootstrap.r2rml_source), and the stored suggestions.
"""
