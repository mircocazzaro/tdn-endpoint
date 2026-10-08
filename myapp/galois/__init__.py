"""Modalita' Galois dell'endpoint.

In questa modalita' l'endpoint non espone dati locali: le sue tabelle sono
viste DuckDB su file Parquet che Galois ripopola interrogando un LLM (Azure
OpenAI) a ogni richiesta di Central. Ontop esegue la query SPARQL sulle viste
appena aggiornate; Galois non vede mai l'SQL che Ontop genera.

- ``prompts``, ``json_utils``, ``scan``: parte di py-galois, vendorizzata;
- ``llm``: client Azure OpenAI;
- ``schema``: schema relazionale standard e mapping fisso verso l'ontologia;
- ``store``: configurazione, viste, file Parquet e aggiornamento.
"""
