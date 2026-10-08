"""Ontologia attiva dell'endpoint e sua sostituzione con una distribuita da Central.

L'ontologia di partenza e' quella del repository (ontop_process.TTL_FILE). Una
ricevuta da Central viene salvata in HDN_STATE_DIR/ontology/active.ttl e da
quel momento e' quella che Ontop carica.

Central distribuisce l'ontologia sempre insieme al template di mapping scritto
per essa (i blocchi modello che *Map Data to HERO* propone). Il template di
partenza e' myapp/mappings/template.obda; quello ricevuto viene salvato in
HDN_STATE_DIR/ontology/template.obda. Il template deve usare solo termini che
la nuova ontologia dichiara, fuori dai vocabolari standard (xsd, rdf, rdfs,
owl), altrimenti l'aggiornamento intero viene rifiutato.

Una nuova ontologia puo' rendere non validi i mapping del sito. Un blocco di
mapping viene eliminato se il suo target usa un termine (classe, proprieta',
individuo) che l'ontologia precedente dichiarava e la nuova non dichiara piu'.
I termini mai dichiarati da nessuna delle due (vocabolari esterni come xsd:)
non contano. I blocchi rimasti restano invariati; il mapping precedente resta
fra le copie di sicurezza.
"""
import hashlib
import json
import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from django.conf import settings

from . import ontop_process
from .obda_mapping import parse_mappings, save_active_mapping, split_collection

audit = logging.getLogger("hdn.audit")

MAX_TTL_BYTES = 8 * 1024 * 1024
_CATALOG_NS = "{urn:oasis:names:tc:entity:xmlns:xml:catalog}"


class InvalidOntology(ValueError):
    pass


class StaleOntology(ValueError):
    def __init__(self, installed):
        super().__init__(f"installed version {installed}")
        self.installed = installed


# --- stato --------------------------------------------------------------------

def _dir():
    return Path(settings.HDN_STATE_DIR) / "ontology"


def active_path():
    """File dell'ontologia che Ontop deve caricare."""
    p = _dir() / "active.ttl"
    return p if p.is_file() else Path(ontop_process.TTL_FILE)


def template_path(default):
    """Template di mapping da proporre: quello ricevuto con l'ontologia, o ``default``."""
    p = _dir() / "template.obda"
    return str(p) if p.is_file() else default


def installed():
    """Versione e hash dell'ontologia e del template attivi (0 = quelli del repository)."""
    try:
        return json.loads((_dir() / "state.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"version": 0, "sha256": None, "template_sha256": None}


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --- analisi ------------------------------------------------------------------

def offline_imports():
    """IRI degli owl:imports risolvibili senza rete (catalogo XML di Ontop)."""
    root = ET.parse(ontop_process.XML_CATALOG).getroot()
    return {e.get("name") for e in root.iter(_CATALOG_NS + "uri")}


def parse(ttl_text):
    """Grafo rdflib dell'ontologia; InvalidOntology se il Turtle non e' valido."""
    import rdflib
    try:
        return rdflib.Graph().parse(data=ttl_text, format="turtle")
    except Exception as exc:  # rdflib solleva tipi diversi a seconda dell'errore
        raise InvalidOntology(f"not valid Turtle: {str(exc)[:300]}")


def declared_terms(graph):
    """IRI dichiarati dall'ontologia (classi, proprieta', individui)."""
    import rdflib
    from rdflib.namespace import OWL, RDF, RDFS
    kinds = (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
             RDF.Property, RDFS.Class, RDFS.Datatype, OWL.NamedIndividual)
    return {str(s) for k in kinds for s in graph.subjects(RDF.type, k)
            if isinstance(s, rdflib.URIRef)}


_import_terms_cache = {}


def offline_import_terms(graph):
    """Termini dichiarati dagli owl:imports di ``graph``, letti dalle copie offline.

    Es. skos:broaderTransitive e' dichiarato da skos-core.rdf, non da HERO.
    """
    import rdflib
    root = ET.parse(ontop_process.XML_CATALOG).getroot()
    local = {e.get("name"): Path(ontop_process.XML_CATALOG).parent / e.get("uri")
             for e in root.iter(_CATALOG_NS + "uri")}
    out = set()
    for iri in imports(graph):
        path = local.get(iri)
        if path is None or not path.is_file():
            continue
        key = (str(path), path.stat().st_mtime_ns)
        if key not in _import_terms_cache:
            g = rdflib.Graph()
            g.parse(str(path), format=rdflib.util.guess_format(str(path)) or "xml")
            _import_terms_cache[key] = frozenset(declared_terms(g))
        out |= _import_terms_cache[key]
    return out


def imports(graph):
    from rdflib.namespace import OWL
    return {str(o) for o in graph.objects(None, OWL.imports)}


_PREFIX_LINE_RE = re.compile(r"^\s*([A-Za-z][\w-]*)?:\s+(\S+)\s*$")
_LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_FULL_IRI_RE = re.compile(r"<([^<>{}\s]*)>(?!\{)")
# nome prefissato non seguito da "{" (altrimenti e' un template di IRI)
_PNAME_RE = re.compile(r"(?<![\w:{}<])([A-Za-z][\w-]*)?:([A-Za-z_][\w-]*(?:\.[\w-]+)*)(?![\w{])")


def mapping_prefixes(header):
    out = {}
    if "[PrefixDeclaration]" in header:
        for line in header.split("[PrefixDeclaration]", 1)[1].splitlines():
            m = _PREFIX_LINE_RE.match(line)
            if m:
                out[m.group(1) or ""] = m.group(2)
    return out


def target_terms(target, prefixes):
    """IRI costanti citati nel target di un mapping (literal e template esclusi)."""
    text = _LITERAL_RE.sub(" ", target)
    terms = set(_FULL_IRI_RE.findall(text))
    for p, local in _PNAME_RE.findall(_FULL_IRI_RE.sub(" ", text)):
        if p in prefixes:
            terms.add(prefixes[p] + local)
    return terms


# Vocabolari standard: i loro termini (es. xsd:float) non devono essere
# dichiarati dall'ontologia.
STANDARD_NAMESPACES = frozenset({
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
})


def namespace(iri):
    cut = max(iri.rfind("#"), iri.rfind("/"))
    return iri[:cut + 1]


def undeclared_terms(obda_text, terms):
    """``{termine: [mappingId]}`` dei termini usati nei target ma non dichiarati.

    Contano solo i termini nei namespace in cui l'ontologia dichiara qualcosa
    (es. bto:), esclusi i vocabolari standard: un IRI di un vocabolario
    esterno, come una costante NCIT, non e' un errore.
    """
    header, _ = split_collection(obda_text)
    prefixes = mapping_prefixes(header)
    spaces = {namespace(t) for t in terms} - STANDARD_NAMESPACES
    out = {}
    for block in parse_mappings(obda_text):
        for t in sorted(target_terms(block.target, prefixes)):
            if t not in terms and namespace(t) in spaces:
                out.setdefault(t, []).append(block.mapping_id)
    return out


def validate_template(text, terms):
    """InvalidOntology se ``text`` non e' un template di mapping valido per ``terms``."""
    try:
        blocks = parse_mappings(text)
    except ValueError as exc:
        raise InvalidOntology(f"mapping template: {exc}")
    if not blocks:
        raise InvalidOntology("mapping template: no mapping block")
    ids = [b.mapping_id for b in blocks]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise InvalidOntology("mapping template: duplicate mappingId " + ", ".join(dup))
    bad = undeclared_terms(text, terms)
    if bad:
        detail = "; ".join(f"{t} ({', '.join(ids[:3])})" for t, ids in sorted(bad.items())[:10])
        raise InvalidOntology("mapping template uses terms the ontology does not declare: " + detail)
    return blocks


def filter_mapping(obda_text, removed):
    """``(testo_nuovo, mantenuti, eliminati)``; eliminati = [(id, [termini])].

    ``testo_nuovo`` e' None se non resta alcun blocco.
    """
    header, _ = split_collection(obda_text)
    prefixes = mapping_prefixes(header)
    kept, dropped = [], []
    for block in parse_mappings(obda_text):
        bad = sorted(target_terms(block.target, prefixes) & removed)
        (dropped if bad else kept).append((block, bad))
    if not kept:
        return None, [], [(b.mapping_id, bad) for b, bad in dropped]
    lines = [header.rstrip(), "", "[MappingDeclaration] @collection [["]
    for b, _ in kept:
        lines += [f"mappingId\t{b.mapping_id}", f"target\t\t{b.target}",
                  f"source\t\t{b.source}", ""]
    lines.append("]]")
    return ("\n".join(lines) + "\n", [b.mapping_id for b, _ in kept],
            [(b.mapping_id, bad) for b, bad in dropped])


# --- installazione ----------------------------------------------------------

def _utf8(data, what, limit=MAX_TTL_BYTES):
    if len(data) > limit:
        raise InvalidOntology(f"{what} too large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidOntology(f"{what} is not UTF-8")


def install(ttl_bytes, version, template_bytes):
    """Rende ``ttl_bytes`` l'ontologia attiva, con il suo template di mapping,
    e adegua il mapping del sito.

    Ontologia e template sono verificati entrambi prima di scrivere qualunque
    file: se uno dei due non e' valido non cambia nulla.

    Restituisce un dizionario con l'esito. Non tocca Ontop: il riavvio spetta
    al chiamante (vedi network.receive_ontology).
    """
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidOntology("version must be an integer >= 1")
    current = installed()
    if version <= current["version"]:
        raise StaleOntology(current["version"])
    text = _utf8(ttl_bytes, "ontology")
    template_text = _utf8(template_bytes, "mapping template")

    new_graph = parse(text)
    new_terms = declared_terms(new_graph)
    if not new_terms:
        raise InvalidOntology("the ontology declares no class or property")
    missing = sorted(imports(new_graph) - offline_imports())
    if missing:
        raise InvalidOntology(
            "owl:imports not available offline (Ontop would not start): " + ", ".join(missing))
    template_blocks = validate_template(template_text, new_terms | offline_import_terms(new_graph))

    old_terms = declared_terms(parse(active_path().read_text(encoding="utf-8")))
    removed = old_terms - new_terms

    obda = Path(ontop_process.OBDA_FILE)
    outcome = {"kept": [], "dropped": [], "mapping": "none"}
    new_obda = None
    if obda.is_file():
        new_obda, kept, dropped = filter_mapping(obda.read_text(encoding="utf-8"), removed)
        outcome.update(kept=kept, dropped=dropped,
                       mapping="unchanged" if not dropped else ("emptied" if new_obda is None else "reduced"))

    # Ontologia: la precedente ricevuta da Central resta fra le copie.
    d = _dir()
    if (d / "active.ttl").is_file():
        backups = d / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        os.replace(d / "active.ttl", backups / f"v{current['version']}.ttl")
    if (d / "template.obda").is_file():
        backups = d / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        os.replace(d / "template.obda", backups / f"v{current['version']}.template.obda")
    _atomic_write(d / "active.ttl", ttl_bytes)
    _atomic_write(d / "template.obda", template_bytes)
    _atomic_write(d / "state.json", json.dumps({
        "version": version, "sha256": hashlib.sha256(ttl_bytes).hexdigest(),
        "template_sha256": hashlib.sha256(template_bytes).hexdigest()}).encode())

    # Mapping: copia di sicurezza in ogni caso in cui cambia.
    if outcome["mapping"] in ("reduced", "emptied"):
        backup_dir = os.path.join(os.path.dirname(obda), "mapping-backups")
        if new_obda is not None:
            save_active_mapping(str(obda), new_obda, backup_dir)
        else:
            os.makedirs(backup_dir, exist_ok=True)
            os.replace(obda, os.path.join(backup_dir, f"{obda.stem}.before-ontology-v{version}.obda"))
    outcome["removed_terms"] = len(removed)
    outcome["template_blocks"] = len(template_blocks)
    audit.info("ontology-installed version=%s removed_terms=%d mapping=%s dropped=%d",
               version, len(removed), outcome["mapping"], len(outcome["dropped"]))
    return outcome
