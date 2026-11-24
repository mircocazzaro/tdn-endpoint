"""Supporto ai test del protocollo hdn-components v1.

Contiene i tre pezzi che il consumatore (Central) deve avere e che qui servono
da oracolo, tutti senza backend reale:

- ``site_backend(graph)``: ``run(query, timeout)`` che risponde in SPARQL JSON
  dal grafo di un sito, al posto di Ontop/GraphDB;
- ``merge_sites(decoded_list)``: fusione per ``merge_mode`` fra siti;
- ``evaluate(query, relations)``: valuta l'algebra della madre sostituendo i
  BGP con le relazioni ricostruite dalle componenti (join per variabili
  comuni, molteplicita' moltiplicate), ``oracle(query, graphs)`` la valuta
  sull'unione dei grafi.

Le fixture serializzate stanno in ``fixtures/hdn_components``: contengono
madre, contesto, piano, risposte dei siti e risultato atteso, cosi' Central le
consuma senza il backend dell'endpoint. ``HDN_UPDATE_FIXTURES=1`` le riscrive.
"""

import json
import os
from collections import Counter
from pathlib import Path

from rdflib import Graph, Literal, URIRef, Variable
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.evaluate import evalQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.processor import SPARQLResult
from rdflib.plugins.sparql.parserutils import CompValue

from myapp import hdn_components as hc

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hdn_components"


def graph_of(turtle):
    g = Graph()
    g.parse(data=turtle, format="turtle")
    return g


def site_backend(graph, calls=None):
    """``run(query, timeout)`` sul grafo del sito; registra le query in ``calls``."""
    def run(query, timeout):
        if calls is not None:
            calls.append(query)
        return json.loads(graph.query(query).serialize(format="json"))
    return run


def union(graphs):
    g = Graph()
    for x in graphs:
        for t in x:
            g.add(t)
    return g


def merge_sites(decoded_list):
    """``{component_id: Counter}`` fondendo le risposte dei siti per merge_mode."""
    plan = decoded_list[0].plan
    out = {}
    for comp in plan["components"]:
        cid = comp["id"]
        merged = Counter()
        for d in decoded_list:
            rel = d.relations[cid]
            if comp["merge_mode"] == "site_unique_bag":
                merged.update(rel)
            else:                                   # global_witness_set
                for binding in rel:
                    merged[binding] = 1
        out[cid] = merged
    return out


def _join(rows_a, vars_a, rows_b, vars_b):
    shared = [v for v in vars_a if v in vars_b]
    vars_out = vars_a + [v for v in vars_b if v not in vars_a]
    out = Counter()
    for ra, na in rows_a.items():
        da = dict(zip(vars_a, ra))
        for rb, nb in rows_b.items():
            db = dict(zip(vars_b, rb))
            if all(da[v] == db[v] for v in shared):
                row = {**da, **db}
                out[tuple(row[v] for v in vars_out)] += na * nb
    return out, vars_out


def bgp_relation(plan, relations, bgp_id):
    """(righe, variabili) del BGP: join delle sue componenti. Senza componenti: unita'."""
    rel, vars_ = Counter({(): 1}), []
    for comp in plan["components"]:
        if comp["bgp_id"] == bgp_id:
            rel, vars_ = _join(rel, vars_, relations[comp["id"]], comp["vars"])
    return rel, vars_


def evaluate(query, plan, relations):
    """Risultato della madre con i BGP sostituiti dalle relazioni ricostruite."""
    q = translateQuery(parseQuery(query))
    nodes = {tuple(path): node for path, node in hc.bgp_nodes(q.algebra)}
    for b in plan["bgps"]:
        rel, vars_ = bgp_relation(plan, relations, b["id"])
        rows = []
        for binding, n in rel.items():
            row = {Variable(v): t for v, t in zip(vars_, binding) if t is not None}
            rows += [row] * n
        values = CompValue("values", res=rows)
        node = nodes[tuple(b["path"])]
        node.clear()
        node.name = "ToMultiSet"
        node["p"] = values
    return _solutions(SPARQLResult(evalQuery(Graph(), q, {}, None)))


def oracle(query, graphs):
    """Risultato della madre sull'unione dei grafi."""
    return _solutions(union(graphs).query(query))


def _solutions(result):
    if result.type == "ASK":
        return Counter({("ASK", bool(result.askAnswer)): 1})
    out = Counter()
    for row in result:
        out[tuple(None if v is None else v.n3() for v in row)] += 1
    return out


# --- fixture serializzate ------------------------------------------------------

def write_or_check_fixture(testcase, name, document):
    """Confronta ``document`` con la fixture ``name`` (la riscrive con HDN_UPDATE_FIXTURES=1)."""
    path = FIXTURES / f"{name}.json"
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("HDN_UPDATE_FIXTURES") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    testcase.assertTrue(path.is_file(), f"fixture mancante: {path} (HDN_UPDATE_FIXTURES=1)")
    testcase.assertEqual(json.loads(path.read_text(encoding="utf-8")), json.loads(text),
                         f"fixture {name} diversa da quella generata")


def expected_rows(counter):
    """Risultato atteso in forma serializzabile (lista ordinata di righe con molteplicita')."""
    return [{"row": list(r), "count": n} for r, n in sorted(counter.items(), key=lambda kv: str(kv[0]))]
