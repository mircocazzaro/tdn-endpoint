"""Contratto hdn-components v1: parte pura, condivisa con HDN Central.

Nessuna dipendenza da Django, dal backend o dai dati. Contiene

- l'identificazione deterministica dei BGP e degli atomi nell'algebra RDFLib
  della query di valutazione (``bgp_nodes``);
- la costruzione del ``plan`` a partire da una partizione degli atomi
  (``build_plan``) e i digest (``plan_digest``, ``query_digest``);
- la codifica dei termini (``encode_term`` / ``decode_term``);
- l'involucro della risposta (``envelope``, ``empty_envelope``) e il
  validatore/decoder lato consumatore (``decode``), che e' lo stesso codice che
  Central deve eseguire: sta qui perche' i fixture lo usano per verificarsi.

Regole di ordinamento fissate dal contratto e da questa implementazione
(Central deve usare le stesse):

- id dei BGP ``b0, b1...`` nell'ordine di visita dell'algebra, chiavi ordinate
  (escluse ``PV`` e ``_vars``) e indici crescenti; ``path`` = chiavi e indici;
- dentro un BGP i pattern sono ordinati per la loro codifica JSON canonica;
  ``bN.aK`` e' l'atomo di posizione K (le occorrenze ripetute restano);
- ``atom_ids`` di una componente seguono la posizione degli atomi (K
  crescente); le componenti sono ordinate confrontando le liste di id come
  liste di stringhe (``["b0.a0","b0.a1"] < ["b0.a2"]``) e numerate ``bN.cK``;
- ``vars`` = variabili esportate, un sottoinsieme (anche tutte) di quelle degli
  atomi coperti, nell'ordine della prima comparsa scorrendo gli atomi e, in
  ciascuno, soggetto, predicato, oggetto. Il planner esporta le *variabili di
  interfaccia* (usate fuori dalla componente: altre componenti, testa, filtri,
  aggregati, altri BGP) con le molteplicita' dei binding, e tutte le variabili
  per le componenti ``global_witness_set``.
"""

import hashlib
import json
import re
from collections import Counter

from rdflib import BNode, Literal, URIRef, Variable
from rdflib.paths import Path as RdfPath
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery
from rdflib.plugins.sparql.parserutils import CompValue

PROTOCOL = "hdn-components"
VERSION = 1
ALGEBRA_VERSION = "rdflib-7.6.0-hdn-1"
PLANNER_VERSION = "1"
MERGE_MODES = ("site_unique_bag", "global_witness_set")

_SKIP_KEYS = ("PV", "_vars")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"[1-9][0-9]*")


class Unplannable(ValueError):
    """La query non e' pianificabile: formato canonico con ``plan: null``."""


class InvalidResponse(ValueError):
    """Risposta v1 non utilizzabile dal consumatore."""


# --- digest ------------------------------------------------------------------

def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def query_digest(query):
    """SHA-256 di ``query.strip()`` in UTF-8."""
    return sha256_text(query.strip())


def plan_digest(plan):
    return sha256_text(canonical_json(plan))


# --- termini -----------------------------------------------------------------

def encode_term(t):
    """Termine RDFLib o variabile nella codifica del piano e dei risultati."""
    if isinstance(t, Variable):
        return {"type": "var", "value": str(t)}
    if isinstance(t, URIRef):
        return {"type": "uri", "value": str(t)}
    if isinstance(t, Literal):
        out = {"type": "literal", "value": str(t)}
        if t.language:
            out["xml:lang"] = t.language
        elif t.datatype:
            out["datatype"] = str(t.datatype)
        return out
    if isinstance(t, BNode):
        raise Unplannable("blank node nei pattern: etichette non deterministiche")
    raise Unplannable(f"termine non supportato: {type(t).__name__}")


def decode_term(d):
    """Inverso di ``encode_term`` (anche per i termini dei risultati SPARQL)."""
    if not isinstance(d, dict) or "type" not in d or "value" not in d:
        raise InvalidResponse("termine RDF malformato")
    kind, value = d["type"], d["value"]
    if not isinstance(value, str):
        raise InvalidResponse("il valore di un termine deve essere una stringa")
    if kind == "var":
        return Variable(value)
    if kind == "uri":
        return URIRef(value)
    if kind == "bnode":
        return BNode(value)
    if kind in ("literal", "typed-literal"):
        lang, dt = d.get("xml:lang"), d.get("datatype")
        return Literal(value, lang=lang) if lang else Literal(value, datatype=URIRef(dt) if dt else None)
    raise InvalidResponse(f"tipo di termine sconosciuto: {kind!r}")


def normalize_result_term(d):
    """Termine di un risultato SPARQL JSON nella forma del contratto.

    Preserva type, value, datatype e xml:lang; ``typed-literal`` (vecchi
    motori) diventa ``literal``. ``None`` resta ``None`` (non associata).
    """
    if d is None:
        return None
    kind = d.get("type")
    if kind == "typed-literal":
        kind = "literal"
    if kind not in ("uri", "literal", "bnode"):
        raise ValueError(f"tipo di termine non valido: {kind!r}")
    out = {"type": kind, "value": d["value"]}
    if kind == "literal":
        if d.get("xml:lang"):
            out["xml:lang"] = d["xml:lang"]
        elif d.get("datatype"):
            out["datatype"] = d["datatype"]
    return out


# --- algebra e BGP -----------------------------------------------------------

def algebra_of(text):
    try:
        return translateQuery(parseQuery(text)).algebra
    except Exception as exc:  # pyparsing e rdflib sollevano molti tipi
        raise Unplannable(f"SPARQL non analizzabile: {str(exc).splitlines()[0][:200]}")


def _walk(node, path, out):
    if isinstance(node, CompValue):
        if node.name == "BGP":
            out.append((path, node))
            return
        for key in sorted(k for k in node.keys() if k not in _SKIP_KEYS):
            _walk(node[key], path + [key], out)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk(item, path + [i], out)


def bgp_nodes(algebra):
    """``[(path, nodo BGP)]`` nell'ordine di visita del contratto."""
    out = []
    _walk(algebra, [], out)
    return out


def node_at(algebra, path):
    node = algebra
    for step in path:
        node = node[step]
    return node


def _triple_json(triple):
    return [encode_term(t) for t in triple]


def bgp_atoms(node, bgp_id):
    """Atomi del BGP: ``[{"id", "pattern"}]`` ordinati e numerati."""
    for tr in node.triples:
        if isinstance(tr[1], RdfPath):
            raise Unplannable("property path nei pattern")
    patterns = sorted((_triple_json(t) for t in node.triples), key=canonical_json)
    return [{"id": f"{bgp_id}.a{i}", "pattern": p} for i, p in enumerate(patterns)]


def check_supported(algebra):
    """Solleva ``Unplannable`` per costrutti fuori dal frammento v1."""
    seen = []

    def walk(n):
        if isinstance(n, CompValue):
            if n.name in ("Builtin_EXISTS", "Builtin_NOTEXISTS"):
                seen.append("EXISTS/NOT EXISTS")
            elif n.name in ("Graph", "ServiceGraphPattern", "Service"):
                seen.append(n.name)
            for k, v in n.items():
                if k not in _SKIP_KEYS:
                    walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(algebra)
    if algebra.name not in ("SelectQuery", "AskQuery"):
        raise Unplannable(f"forma di query non supportata: {algebra.name}")
    if seen:
        raise Unplannable("costrutto non supportato: " + ", ".join(sorted(set(seen))))


def atom_vars(pattern):
    """Variabili di un pattern JSON, in ordine s, p, o, senza ripetizioni."""
    return list(dict.fromkeys(t["value"] for t in pattern if t["type"] == "var"))


def component_vars(atoms):
    seen = []
    for a in atoms:
        for v in atom_vars(a["pattern"]):
            if v not in seen:
                seen.append(v)
    return seen


def vars_outside(algebra, skip):
    """Nomi delle variabili che compaiono nell'algebra fuori dal nodo ``skip``.

    Comprende proiezione (``PV``), filtri, aggregati, ordinamenti, BIND e gli
    altri BGP; esclude i metadati ``_vars`` (variabili in scope, che
    includerebbero quelle del solo BGP).
    """
    out = set()

    def walk(n):
        if n is skip:
            return
        if isinstance(n, Variable):
            out.add(str(n))
        elif isinstance(n, CompValue):
            for k, v in n.items():
                if k != "_vars":
                    walk(v)
        elif isinstance(n, (list, tuple)):
            for x in n:
                walk(x)

    walk(algebra)
    return out


def build_plan(evaluation_query, partition):
    """Piano v1 per ``evaluation_query``.

    ``partition(bgp_id, atoms)`` restituisce una lista di componenti
    ``(indici di atomo, merge_mode)`` oppure ``(indici, merge_mode, minimize)``
    che coprono gli atomi esattamente una volta. Con ``minimize`` la
    componente esporta solo le variabili di interfaccia; altrimenti tutte
    quelle dei suoi atomi.
    """
    algebra = algebra_of(evaluation_query)
    check_supported(algebra)
    bgps, components = [], []
    for i, (path, node) in enumerate(bgp_nodes(algebra)):
        bid = f"b{i}"
        atoms = bgp_atoms(node, bid)
        bgps.append({"id": bid, "path": path, "atoms": atoms})
        comps, covered = [], []
        for item in partition(bid, atoms):
            idx, mode = sorted(item[0]), item[1]
            minimize = len(item) > 2 and item[2]
            if mode not in MERGE_MODES:
                raise ValueError(f"merge_mode sconosciuto: {mode}")
            covered += idx
            comps.append({"bgp_id": bid, "atom_ids": [atoms[k]["id"] for k in idx],
                          "vars": component_vars([atoms[k] for k in idx]),
                          "merge_mode": mode, "_min": minimize})
        if sorted(covered) != list(range(len(atoms))):
            raise ValueError(f"{bid}: le componenti non coprono gli atomi esattamente una volta")
        outside = vars_outside(algebra, node)
        all_vars = [c["vars"] for c in comps]
        for n, c in enumerate(comps):
            if c.pop("_min"):
                others = {v for m, vs in enumerate(all_vars) if m != n for v in vs}
                c["vars"] = [v for v in c["vars"] if v in outside or v in others]
        comps.sort(key=lambda c: c["atom_ids"])
        for j, c in enumerate(comps):
            components.append({"id": f"{bid}.c{j}", **c})
    return {"evaluation_query": evaluation_query, "algebra_version": ALGEBRA_VERSION,
            "bgps": bgps, "components": components}


# --- risposta ----------------------------------------------------------------

def context(catalog_sha256, ontology_sha256, profile_sha256):
    return {"catalog_sha256": catalog_sha256, "ontology_sha256": ontology_sha256,
            "profile_sha256": profile_sha256, "planner_version": PLANNER_VERSION}


def envelope(query, ctx, plan, pool=(), memberships=()):
    """Risposta v1. ``plan`` None per le query non pianificabili."""
    return {
        "protocol": PROTOCOL,
        "version": VERSION,
        "query_sha256": query_digest(query),
        "context": ctx,
        "plan": plan,
        "plan_sha256": None if plan is None else plan_digest(plan),
        "tuple_pool": list(pool),
        "memberships": list(memberships),
    }


def empty_envelope(query, ctx, plan):
    """Mancato contributo canonico: stessa struttura pubblica, pool e membership vuoti."""
    return envelope(query, ctx, plan)


class PoolBuilder:
    """Accumula tuple e membership: ogni vettore una volta, un conteggio per coppia."""

    def __init__(self):
        self.pool, self._index, self._counts = [], {}, {}

    def add(self, component_id, columns, vector, multiplicity=1):
        """Aggiunge ``multiplicity`` copie del binding ``vector`` (termini o None)
        per ``component_id``. ``columns`` = posizioni del vettore per le vars."""
        key = canonical_json(vector)
        idx = self._index.get(key)
        if idx is None:
            idx = self._index[key] = len(self.pool)
            self.pool.append(vector)
        k = (component_id, idx, tuple(columns))
        self._counts[k] = self._counts.get(k, 0) + int(multiplicity)

    def memberships(self):
        return [{"component_id": c, "tuple_index": i, "column_indices": list(cols),
                 "multiplicity": str(n)}
                for (c, i, cols), n in sorted(self._counts.items())]


# --- decoder e validatore (lato consumatore) ----------------------------------

class Decoded:
    """Risposta validata: relazioni per componente come ``Counter`` di binding."""

    def __init__(self, envelope_, plan, relations):
        self.envelope = envelope_
        self.plan = plan
        self.relations = relations        # component_id -> Counter{tuple(term|None): n}

    def relation(self, component_id):
        return self.relations[component_id]


def _check_hex(value, what):
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise InvalidResponse(f"{what} non e' un digest SHA-256 esadecimale")


def validate_plan(plan):
    """Controlli sul piano, senza dati; restituisce l'algebra della query di valutazione."""
    if not isinstance(plan, dict):
        raise InvalidResponse("plan mancante")
    if plan.get("algebra_version") != ALGEBRA_VERSION:
        raise InvalidResponse("algebra_version non supportata")
    text = plan.get("evaluation_query")
    if not isinstance(text, str):
        raise InvalidResponse("evaluation_query mancante")
    try:
        algebra = algebra_of(text)
    except Unplannable as exc:
        raise InvalidResponse(str(exc))
    nodes = bgp_nodes(algebra)
    bgps = plan.get("bgps")
    if not isinstance(bgps, list) or len(bgps) != len(nodes):
        raise InvalidResponse("i BGP del piano non corrispondono alla query di valutazione")
    atoms_by_id = {}
    for i, (b, (path, node)) in enumerate(zip(bgps, nodes)):
        if b.get("id") != f"b{i}" or b.get("path") != path:
            raise InvalidResponse(f"BGP b{i}: id o path non corrispondono")
        try:
            expected = bgp_atoms(node, f"b{i}")
        except Unplannable as exc:
            raise InvalidResponse(str(exc))
        if b.get("atoms") != expected:
            raise InvalidResponse(f"BGP b{i}: gli atomi non corrispondono al BGP")
        for a in expected:
            atoms_by_id[a["id"]] = (f"b{i}", a)
    comps = plan.get("components")
    if not isinstance(comps, list):
        raise InvalidResponse("components mancante")
    covered = Counter()
    ids = []
    per_bgp = {}
    for c in comps:
        if not isinstance(c, dict) or set(c) != {"id", "bgp_id", "atom_ids", "vars", "merge_mode"}:
            raise InvalidResponse("definizione di componente malformata")
        if c["merge_mode"] not in MERGE_MODES:
            raise InvalidResponse(f"merge_mode sconosciuto: {c['merge_mode']!r}")
        atoms = []
        for aid in c["atom_ids"]:
            if aid not in atoms_by_id or atoms_by_id[aid][0] != c["bgp_id"]:
                raise InvalidResponse(f"atomo {aid!r} non appartiene a {c['bgp_id']}")
            covered[aid] += 1
            atoms.append(atoms_by_id[aid][1])
        if c["atom_ids"] != [a["id"] for a in sorted(atoms, key=lambda a: int(a["id"].rsplit("a", 1)[1]))]:
            raise InvalidResponse(f"{c['id']}: atom_ids non ordinati")
        full = component_vars(atoms)
        if (not isinstance(c["vars"], list) or len(set(c["vars"])) != len(c["vars"])
                or c["vars"] != [v for v in full if v in c["vars"]]):
            raise InvalidResponse(f"{c['id']}: vars non sono un sottoinsieme ordinato di quelle degli atomi")
        ids.append(c["id"])
        per_bgp.setdefault(c["bgp_id"], []).append(c)
    if any(n != 1 for n in covered.values()) or set(covered) != set(atoms_by_id):
        raise InvalidResponse("le componenti non coprono gli atomi esattamente una volta")
    for bid, cs in per_bgp.items():
        if [c["atom_ids"] for c in cs] != sorted(c["atom_ids"] for c in cs):
            raise InvalidResponse(f"{bid}: componenti non ordinate")
        if [c["id"] for c in cs] != [f"{bid}.c{j}" for j in range(len(cs))]:
            raise InvalidResponse(f"{bid}: id delle componenti non progressivi")
    return algebra


def decode(response, query, expected_context=None, expected_plan_sha256=None):
    """Valida una risposta v1 e ne ricostruisce le relazioni.

    Solleva ``InvalidResponse`` per versione, digest, contesto, piano,
    riferimenti o conteggi non validi. Con ``plan: null`` restituisce
    relazioni vuote e ``plan`` None.
    """
    if not isinstance(response, dict) or response.get("protocol") != PROTOCOL:
        raise InvalidResponse("protocol non riconosciuto")
    if response.get("version") != VERSION or isinstance(response.get("version"), bool):
        raise InvalidResponse("version non supportata")
    if response.get("query_sha256") != query_digest(query):
        raise InvalidResponse("query_sha256 diverso dalla richiesta")
    ctx = response.get("context")
    if not isinstance(ctx, dict) or set(ctx) != {"catalog_sha256", "ontology_sha256",
                                                 "profile_sha256", "planner_version"}:
        raise InvalidResponse("context malformato")
    for k in ("catalog_sha256", "ontology_sha256", "profile_sha256"):
        _check_hex(ctx[k], k)
    if ctx["planner_version"] != PLANNER_VERSION:
        raise InvalidResponse("planner_version non supportata")
    if expected_context is not None and ctx != expected_context:
        raise InvalidResponse("contesto diverso da quello atteso")
    pool, members = response.get("tuple_pool"), response.get("memberships")
    if not isinstance(pool, list) or not isinstance(members, list):
        raise InvalidResponse("tuple_pool o memberships mancante")
    plan = response.get("plan")
    if plan is None:
        if response.get("plan_sha256") is not None or pool or members:
            raise InvalidResponse("risposta senza piano ma con contenuto")
        return Decoded(response, None, {})
    if response.get("plan_sha256") != plan_digest(plan):
        raise InvalidResponse("plan_sha256 non corrisponde al piano")
    if expected_plan_sha256 is not None and response["plan_sha256"] != expected_plan_sha256:
        raise InvalidResponse("piano diverso da quello degli altri siti")
    validate_plan(plan)
    vars_of = {c["id"]: c["vars"] for c in plan["components"]}
    relations = {cid: Counter() for cid in vars_of}
    terms = []
    for vec in pool:
        if not isinstance(vec, list):
            raise InvalidResponse("vettore del pool malformato")
        terms.append([None if t is None else decode_term(t) for t in vec])
    for m in members:
        if not isinstance(m, dict) or set(m) != {"component_id", "tuple_index", "column_indices",
                                                 "multiplicity"}:
            raise InvalidResponse("membership malformata")
        cid = m["component_id"]
        if cid not in vars_of:
            raise InvalidResponse(f"componente sconosciuta: {cid!r}")
        ti, cols, mult = m["tuple_index"], m["column_indices"], m["multiplicity"]
        if isinstance(ti, bool) or not isinstance(ti, int) or not 0 <= ti < len(terms):
            raise InvalidResponse("tuple_index fuori dal pool")
        if not isinstance(cols, list) or len(cols) != len(vars_of[cid]):
            raise InvalidResponse("column_indices con arita' diversa da vars")
        if any(isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < len(terms[ti])
               for c in cols):
            raise InvalidResponse("column_indices fuori dal vettore")
        if not isinstance(mult, str) or not _DECIMAL.fullmatch(mult):
            raise InvalidResponse("multiplicity deve essere una stringa decimale positiva")
        binding = tuple(terms[ti][c] for c in cols)
        relations[cid][binding] += int(mult)
    return Decoded(response, plan, relations)
