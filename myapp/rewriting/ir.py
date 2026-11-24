"""Intermediate representation of catalog templates and user queries.

A query is reduced to the fragment the rewriting understands:

    SELECT [DISTINCT] vars | aggregates   or   ASK
    WHERE  { basic graph pattern . FILTER(comparisons, &&, ||, !) }
    [GROUP BY vars] [ORDER BY vars] [LIMIT n] [OFFSET n]

Anything else (OPTIONAL, UNION, BIND, sub-queries, functions...) raises
``Unsupported``. Templates outside the fragment can still contribute their
triple patterns to the lattice (``Template.triples``) but cannot be matched
as a whole.

Template parameters (``{disease}``, ``"{sex}"``, ``<...UATC/{atc}>``) are
replaced by variables ``?__p_<name>`` before parsing; ``Slot`` records how
the parameter is written, so that a constant of the user query can be turned
back into the value the template expects.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

from rdflib import Literal, URIRef, Variable
from rdflib.namespace import RDF, XSD
from rdflib.plugins.sparql.algebra import translateQuery
from rdflib.plugins.sparql.parser import parseQuery

from ..catalog import PARAM_TYPES, PROLOGUE

# Port di central-tdn/catalogapp/rewriting/ir.py. Differenze volute:
# - il catalogo arriva come dato (le entry del documento attivo), con il tipo
#   di ciascun parametro nel campo ``params``, invece di una tabella globale;
# - le grammatiche dei parametri sono quelle dell'endpoint (PARAM_TYPES), le
#   stesse con cui l'endpoint valida i valori.
WIRE_GRAMMARS = {name: t.pattern for name, t in PARAM_TYPES.items()}

PARAM_PREFIX = "__p_"
PREFIXES = dict(re.findall(r"PREFIX\s+([A-Za-z][\w-]*):\s*<([^>]*)>", PROLOGUE))
NUMERIC = {XSD.integer, XSD.decimal, XSD.double, XSD.float, XSD.int, XSD.long,
           XSD.short, XSD.nonNegativeInteger, XSD.positiveInteger}


class Unsupported(ValueError):
    """The query uses SPARQL outside the rewriting fragment."""


@dataclass(frozen=True)
class Slot:
    """How a template parameter appears in the template text."""
    name: str
    kind: str            # 'term' | 'literal' | 'iri'
    wire: Optional[str]  # grammar name on the endpoints
    prefix: str = ""
    suffix: str = ""

    @property
    def var(self):
        return Variable(PARAM_PREFIX + self.name)

    def text(self):
        if self.kind == "literal":
            return '"{%s}"' % self.name
        if self.kind == "iri":
            return "<%s{%s}%s>" % (self.prefix, self.name, self.suffix)
        return "{%s}" % self.name

    def value_for(self, term):
        """Value to substitute for ``term`` (a constant of the user query), or None."""
        candidates = []
        if self.kind == "literal" and isinstance(term, Literal):
            candidates = [str(term)]
        elif self.kind == "iri" and isinstance(term, URIRef):
            s = str(term)
            if s.startswith(self.prefix) and s.endswith(self.suffix):
                candidates = [s[len(self.prefix):len(s) - len(self.suffix) or None]]
        elif self.kind == "term":
            if isinstance(term, URIRef):
                candidates = [render_iri(term), "<%s>" % term]
            elif isinstance(term, Literal):
                candidates = [str(term)]
        grammar = WIRE_GRAMMARS.get(self.wire)
        for c in candidates:
            if grammar and re.fullmatch(grammar, c):
                return c
        return None


@dataclass(frozen=True)
class Aggregate:
    fn: str                  # COUNT | SUM | AVG | MIN | MAX
    var: Optional[Variable]  # None for COUNT(*)
    distinct: bool
    alias: Variable


@dataclass
class QueryIR:
    form: str                                   # 'SELECT' | 'ASK'
    triples: list
    filters: list = field(default_factory=list)  # expression trees, see _expr
    projection: list = field(default_factory=list)  # Variables (incl. aggregate aliases)
    aggregates: list = field(default_factory=list)
    group_by: list = field(default_factory=list)
    distinct: bool = False
    order_by: list = field(default_factory=list)  # (Variable, descending)
    limit: Optional[int] = None
    offset: Optional[int] = None

    def variables(self):
        out = []
        for t in self.triples:
            for x in t:
                if isinstance(x, Variable) and x not in out:
                    out.append(x)
        return out


@dataclass
class Template:
    """A catalog template, as the rewriting sees it."""
    key: str
    level: int
    description: str
    text: str                  # template text with {placeholders}
    params: list               # parameter names
    slots: dict                # name -> Slot
    triples: list              # all triple patterns (for the lattice)
    ir: Optional[QueryIR]      # None if outside the fragment
    whole_reason: str = ""     # why ``ir`` is None
    derived: bool = False
    parents: tuple = ()


# --- term rendering ---------------------------------------------------------

_LOCAL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def render_iri(iri):
    s = str(iri)
    if iri == RDF.type:
        return "a"
    for p, ns in sorted(PREFIXES.items(), key=lambda kv: -len(kv[1])):
        if s.startswith(ns) and _LOCAL_RE.fullmatch(s[len(ns):]):
            return f"{p}:{s[len(ns):]}"
    return f"<{s}>"


def render_term(t, slots_by_var=None):
    if isinstance(t, Variable):
        if slots_by_var and t in slots_by_var:
            return slots_by_var[t].text()
        return "?" + str(t)
    if isinstance(t, URIRef):
        return render_iri(t)
    if isinstance(t, Literal):
        if t.datatype in NUMERIC or t.datatype == XSD.boolean:
            return str(t) if t.datatype != XSD.boolean else str(t).lower()
        return t.n3()
    raise Unsupported(f"term {t!r}")


# --- template preprocessing ------------------------------------------------

_LIT_SLOT = re.compile(r'"\{([A-Za-z_]\w*)\}"')
_IRI_SLOT = re.compile(r'<([^<>{}\s]*)\{([A-Za-z_]\w*)\}([^<>{}\s]*)>')
_LIT_WITH_SLOT = re.compile(r'"[^"\n]*\{[A-Za-z_]\w*\}[^"\n]*"')
_BARE_SLOT = re.compile(r'\{([A-Za-z_]\w*)\}')


def preprocess_template(text, wire=None):
    """``(sparql_with_param_vars, slots, composite)``; ``wire``: parametro -> tipo."""
    slots = {}
    wire = wire or {}

    def wire_type(name):
        return wire.get(name)

    def lit(m):
        slots[m.group(1)] = Slot(m.group(1), "literal", wire_type(m.group(1)))
        return "?" + PARAM_PREFIX + m.group(1)

    def iri(m):
        slots[m.group(2)] = Slot(m.group(2), "iri", wire_type(m.group(2)), m.group(1), m.group(3))
        return "?" + PARAM_PREFIX + m.group(2)

    s = _LIT_SLOT.sub(lit, text)
    s = _IRI_SLOT.sub(iri, s)
    composite = bool(_LIT_WITH_SLOT.search(s))
    s = _LIT_WITH_SLOT.sub('"__slot__"', s)

    def bare(m):
        slots.setdefault(m.group(1), Slot(m.group(1), "term", wire_type(m.group(1))))
        return "?" + PARAM_PREFIX + m.group(1)

    s = _BARE_SLOT.sub(bare, s)
    return s, slots, composite


# --- algebra walking -------------------------------------------------------

def _algebra(sparql, prologue=PROLOGUE):
    try:
        return translateQuery(parseQuery(prologue + sparql)).algebra
    except Exception as exc:  # pyparsing / rdflib raise many types
        raise Unsupported(f"not valid SPARQL: {str(exc).splitlines()[0][:200]}")


def all_triples(node):
    """Triple patterns of every BGP in the algebra tree (for the lattice)."""
    out = []

    def walk(n):
        if hasattr(n, "name"):
            if n.name == "BGP":
                for t in n.triples:
                    if t not in out:
                        out.append(t)
            for v in n.values():
                walk(v)
        elif isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
    walk(node)
    return out


_CMP = {"<", "<=", ">", ">=", "=", "!="}


def _expr(e):
    """Filter expression tree: ('and'|'or', a, b) | ('not', a) | ('cmp', op, l, r) | term."""
    if isinstance(e, (Variable, URIRef, Literal)):
        return e
    name = getattr(e, "name", None)
    if name in ("ConditionalAndExpression", "ConditionalOrExpression"):
        op = "and" if name.startswith("ConditionalAnd") else "or"
        acc = _expr(e.expr)
        for o in e.other or []:
            acc = (op, acc, _expr(o))
        return acc
    if name == "RelationalExpression":
        if e.op not in _CMP or e.other is None or isinstance(e.other, list):
            raise Unsupported(f"comparison {e.op}")
        return ("cmp", e.op, _expr(e.expr), _expr(e.other))
    if name == "UnaryNot":
        return ("not", _expr(e.expr))
    raise Unsupported(f"FILTER expression {name or type(e).__name__}")


def _agg(a):
    fn = a.name.replace("Aggregate_", "").upper()
    if fn not in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
        raise Unsupported(f"aggregate {fn}")
    var = None if a.vars == "*" else a.vars
    if var is not None and not isinstance(var, Variable):
        raise Unsupported("aggregate over an expression")
    return fn, var, bool(a.distinct)


def to_ir(algebra):
    """QueryIR from rdflib algebra; Unsupported outside the fragment."""
    ir = QueryIR(form="ASK" if algebra.name == "AskQuery" else "SELECT", triples=[])
    if algebra.name not in ("SelectQuery", "AskQuery"):
        raise Unsupported(algebra.name)
    ir.projection = list(algebra.PV) if ir.form == "SELECT" else []
    node = algebra.p
    renames = {}   # internal __agg_n__ -> alias
    aggs = []
    while True:
        n = node.name
        if n == "Slice":
            ir.offset = node.start or None
            ir.limit = node.length
        elif n == "Distinct":
            ir.distinct = True
        elif n == "Project":
            pass
        elif n == "OrderBy":
            for c in node.expr:
                v = getattr(c, "expr", c)
                if not isinstance(v, Variable):
                    raise Unsupported("ORDER BY over an expression")
                ir.order_by.append((v, getattr(c, "order", None) == "DESC"))
        elif n == "Extend":
            if not isinstance(node.expr, Variable) or not str(node.expr).startswith("__agg_"):
                raise Unsupported("BIND or computed projection")
            renames[node.expr] = node.var
        elif n == "AggregateJoin":
            for a in node.A:
                if a.name == "Aggregate_Sample":   # group-by variable carried through
                    renames.setdefault(a.res, a.vars)
                    continue
                aggs.append((a.res, _agg(a)))
        elif n == "Group":
            ir.group_by = list(node.expr or [])
            if any(not isinstance(v, Variable) for v in ir.group_by):
                raise Unsupported("GROUP BY over an expression")
        elif n == "Filter":
            ir.filters.append(_expr(node.expr))
        elif n == "BGP":
            ir.triples = list(node.triples)
            break
        elif n == "Join" and node.p1.name == "BGP" and node.p2.name == "BGP":
            ir.triples = list(node.p1.triples) + list(node.p2.triples)
            break
        else:
            raise Unsupported(n)
        node = node.p
    for res, (fn, var, distinct) in aggs:
        alias = renames.get(res)
        if alias is None or alias not in ir.projection:
            raise Unsupported("aggregate not projected")
        ir.aggregates.append(Aggregate(fn, var, distinct, alias))
    if not ir.triples:
        raise Unsupported("empty pattern")
    for t in ir.triples:
        if isinstance(t[1], Variable):
            raise Unsupported("variable predicate")
    if ir.aggregates or ir.group_by:
        allowed = set(ir.group_by) | {a.alias for a in ir.aggregates}
        if set(ir.projection) - allowed:
            raise Unsupported("projected variable neither grouped nor aggregated")
    return ir


def parse_query(sparql, prologue=PROLOGUE):
    """IR of a user query. Raises Unsupported."""
    return to_ir(_algebra(sparql, prologue))


def parse_template(entry, prologue=PROLOGUE):
    """Template from a published catalog entry (dict with key, level, sparql, params...)."""
    text = entry["sparql"]
    params = entry.get("params")
    pre, slots, composite = preprocess_template(text, params if isinstance(params, dict) else None)
    algebra = _algebra(pre, prologue)
    ir, reason = None, ""
    if composite:
        reason = "parameter inside a larger literal"
    else:
        try:
            ir = to_ir(algebra)
        except Unsupported as exc:
            reason = str(exc)
    return Template(key=entry["key"], level=entry["level"], description=entry.get("description", ""),
                    text=text, params=list(entry.get("params") or slots), slots=slots,
                    triples=all_triples(algebra), ir=ir, whole_reason=reason,
                    derived=bool(entry.get("derived")), parents=tuple(entry.get("parents") or ()))
