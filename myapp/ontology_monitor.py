"""Indice e grafo dell'ontologia attiva, per la pagina Ontology Monitor.

Tutto deriva dal file che Ontop sta usando (ontology.active_path()) e viene
tenuto in cache finche' il file non cambia (mtime e dimensione). Le ontologie
del progetto sono piccole (decine di classi, centinaia di individui): l'indice
intero va al browser, che fa la ricerca; il grafo Mermaid lo costruisce il
server per un solo termine alla volta.
"""
import threading
from collections import Counter
from pathlib import Path

from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from . import ontology

# Ordine di tipo: il primo che dichiara un IRI ne determina il tipo.
_DECLARED = (
    ("class", (OWL.Class, RDFS.Class)),
    ("object_property", (OWL.ObjectProperty,)),
    ("datatype_property", (OWL.DatatypeProperty,)),
    ("annotation_property", (OWL.AnnotationProperty,)),
    ("property", (RDF.Property,)),
)
KINDS = tuple(k for k, _ in _DECLARED) + ("individual",)
MAX_GRAPH_NODES = 40
PROPERTY_KINDS = ("object_property", "datatype_property", "annotation_property", "property")
# Stile Mermaid per tipo: stessi colori dei badge della pagina (Bootstrap).
NODE_STYLE = {
    "class": "kclass",
    "object_property": "kobject",
    "datatype_property": "kdatatype",
    "annotation_property": "kannotation",
    "property": "kproperty",
    "individual": "kindividual",
}
CLASS_DEFS = (
    "  classDef kclass fill:#fff3cd,stroke:#b58105,color:#000",
    "  classDef kobject fill:#cfe2ff,stroke:#0a58ca,color:#000",
    "  classDef kdatatype fill:#d1e7dd,stroke:#198754,color:#000",
    "  classDef kannotation fill:#cff4fc,stroke:#0aa2c0,color:#000",
    "  classDef kproperty fill:#e9ecef,stroke:#495057,color:#000",
    "  classDef kindividual fill:#e2d4f5,stroke:#6f42c1,color:#000",
)

_cache = {}
_lock = threading.Lock()


def _local_name(iri):
    return iri.rstrip("/").rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _text(graph, subject, predicate):
    """Etichetta o commento: preferisce l'inglese, poi il primo letterale."""
    values = [o for o in graph.objects(subject, predicate) if isinstance(o, Literal)]
    english = [str(o) for o in values if o.language and o.language.lower().startswith("en")]
    if english:
        return english[0]
    return str(values[0]) if values else None


def _uris(graph, subject, predicate):
    return sorted({str(o) for o in graph.objects(subject, predicate) if isinstance(o, URIRef)})


def build(ttl_text):
    """Indice (dizionario JSON) del testo Turtle di un'ontologia.

    InvalidOntology se il Turtle non e' valido.
    """
    graph = ontology.parse(ttl_text)

    kinds = {}
    for kind, types in _DECLARED:
        for t in types:
            for s in graph.subjects(RDF.type, t):
                if isinstance(s, URIRef):
                    kinds.setdefault(str(s), kind)
    class_iris = {i for i, k in kinds.items() if k == "class"}
    for s, _, t in graph.triples((None, RDF.type, None)):
        if not isinstance(s, URIRef) or str(s) in kinds:
            continue
        if t == OWL.NamedIndividual or (isinstance(t, URIRef) and str(t) in class_iris):
            kinds[str(s)] = "individual"

    terms = {}
    for iri, kind in kinds.items():
        s = URIRef(iri)
        is_property = "property" in kind
        terms[iri] = {
            "iri": iri,
            "kind": kind,
            "label": _text(graph, s, RDFS.label) or _local_name(iri),
            "comment": _text(graph, s, RDFS.comment),
            "parents": _uris(graph, s, RDFS.subPropertyOf if is_property else RDFS.subClassOf),
            "domain": _uris(graph, s, RDFS.domain),
            "range": _uris(graph, s, RDFS.range),
            "types": [t for t in _uris(graph, s, RDF.type) if t != str(OWL.NamedIndividual)]
            if kind == "individual" else [],
            "children": [],
            "instances": [],
        }
    for t in terms.values():
        for p in t["parents"]:
            if p in terms:
                terms[p]["children"].append(t["iri"])
        for ty in t["types"]:
            if ty in terms:
                terms[ty]["instances"].append(t["iri"])
    for t in terms.values():
        t["children"].sort()
        t["instances"].sort()

    ordered = sorted(terms.values(), key=lambda t: (KINDS.index(t["kind"]), t["label"].lower()))
    return {
        "ontology": sorted(str(s) for s in graph.subjects(RDF.type, OWL.Ontology)
                           if isinstance(s, URIRef)),
        "counts": dict(Counter(t["kind"] for t in ordered)),
        "prefixes": {p: str(ns) for p, ns in graph.namespaces() if p},
        "terms": ordered,
    }


def index(path=None):
    """Indice dell'ontologia attiva (o di ``path``), dalla cache se il file non e' cambiato.

    Il dizionario restituito e' condiviso: non modificarlo.
    """
    path = Path(path) if path is not None else Path(ontology.active_path())
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if key not in _cache:
            _cache.clear()
            _cache[key] = build(path.read_text(encoding="utf-8"))
        return _cache[key]


def _label_for_mermaid(text):
    return text.replace('"', "'").replace("\n", " ")


def graph(data, iri, max_nodes=MAX_GRAPH_NODES):
    """Diagramma Mermaid del termine ``iri`` e dei termini collegati.

    Restituisce ``(testo_mermaid, troncato)``. KeyError se ``iri`` non e' nell'indice.
    """
    by_iri = {t["iri"]: t for t in data["terms"]}
    focus = by_iri.get(iri)
    if focus is None:
        raise KeyError(iri)
    sub = "subPropertyOf" if "property" in focus["kind"] else "subClassOf"

    # (origine, destinazione, etichetta, tratteggiata)
    edges = []
    for p in focus["parents"]:
        if p in by_iri:
            edges.append((focus["iri"], p, sub, False))
    for c in focus["children"]:
        edges.append((c, focus["iri"], sub, False))
    for d in focus["domain"]:
        if d in by_iri:
            edges.append((focus["iri"], d, "domain", True))
    for r in focus["range"]:
        if r in by_iri:
            edges.append((focus["iri"], r, "range", True))
    for ty in focus["types"]:
        if ty in by_iri:
            edges.append((focus["iri"], ty, "type", True))
    if focus["kind"] in ("class", "individual"):
        # proprieta' dichiarate su questa classe (anche datatype), prima delle istanze
        # perche' il limite di nodi non le escluda
        for t in data["terms"]:
            if t["kind"] in PROPERTY_KINDS and focus["iri"] in t["domain"]:
                edges.append((t["iri"], focus["iri"], "domain", True))
    for i in focus["instances"]:
        edges.append((i, focus["iri"], "type", True))

    ids = {focus["iri"]: "n0"}
    truncated = False

    def node_id(node_iri):
        nonlocal truncated
        if node_iri not in ids:
            if len(ids) >= max_nodes:
                truncated = True
                return None
            ids[node_iri] = f"n{len(ids)}"
        return ids[node_iri]

    lines = ["flowchart LR", *CLASS_DEFS]
    body = []
    for src, dst, label, dashed in edges:
        a, b = node_id(src), node_id(dst)
        if a is None or b is None:
            continue
        arrow = "-.->" if dashed else "-->"
        body.append(f"  {a} {arrow}|{_label_for_mermaid(label)}| {b}")
    for node_iri, nid in ids.items():
        t = by_iri[node_iri]
        mark = "★ " if node_iri == focus["iri"] else ""
        style = NODE_STYLE.get(t["kind"], "kclass")
        lines.append(f'  {nid}["{mark}{_label_for_mermaid(t["label"])}"]:::{style}')
    lines.extend(body)
    return "\n".join(lines) + "\n", truncated
