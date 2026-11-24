"""Equivalent rewritings of a user query in terms of catalog templates.

Two ways to answer a query, tried in this order of preference:

1. **Whole match.** The query is an instance of a template in the fragment
   (same pattern up to variable renaming, parameters bound to constants of
   the query, same filters, grouping and aggregates). The template runs as it
   is, aggregates included, on every endpoint.

2. **MiniCon rewriting** over the *row templates*: templates in the fragment
   that return rows (no aggregate, no filter, no DISTINCT), which include all
   derived templates. For each template, every embedding of its pattern into
   the query yields a MiniCon description (MCD) if

   - template constants equal the query terms they map to;
   - each parameter maps to a constant of the query that its grammar accepts
     (binding pattern: a parameter can never be left free);
   - every query variable the rest of the query needs (projection, grouping,
     aggregates, filters, ordering, atoms not covered by this MCD) is the
     image of a variable the template returns, and template variables mapped
     to constants are returned too, so Central can select on them.

   MCDs covering pairwise disjoint sets of atoms whose union is the whole
   pattern form a rewriting (Pottinger & Halevy 2001). Atoms are mapped
   injectively and templates return rows without DISTINCT, so the join of
   their results has exactly the solutions of the query pattern, with the
   same multiplicities: the rewriting is equivalent under bag semantics, and
   COUNT, SUM and AVG computed by Central are exact.

Among the rewritings found, the one with the lowest level is chosen, then
the one with fewest templates.
"""
from dataclasses import dataclass, field

from rdflib import Variable

from .ir import PARAM_PREFIX, render_term


def _is_param(t):
    return isinstance(t, Variable) and str(t).startswith(PARAM_PREFIX)


@dataclass
class Call:
    """One template execution in a plan."""
    template: object
    bindings: dict                 # parameter -> value
    columns: dict                  # template output var -> query var
    selections: list = field(default_factory=list)  # (template var, constant)
    equalities: list = field(default_factory=list)  # (template var, query var)
    covered: tuple = ()            # indices of query atoms


@dataclass
class Plan:
    kind: str                      # 'whole' | 'rewrite'
    calls: list
    query: object
    aliases: dict = field(default_factory=dict)   # whole: template output var -> query output var

    @property
    def level(self):
        return max(c.template.level for c in self.calls)


class NoRewriting(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# --- embeddings ---------------------------------------------------------------

def _embeddings(view_triples, query_triples, slots, injective_vars=True):
    """All injective atom mappings of ``view_triples`` into ``query_triples``.

    Yields ``(theta, bindings, covered)``; theta maps view variables (params
    excluded) to query terms, bindings maps parameter names to values.
    """
    n = len(view_triples)

    def unify(vt, qt, theta, bindings):
        theta, bindings = dict(theta), dict(bindings)
        for v, q in zip(vt, qt):
            if _is_param(v):
                name = str(v)[len(PARAM_PREFIX):]
                if isinstance(q, Variable):
                    return None
                value = slots[name].value_for(q)
                if value is None or bindings.get(name, value) != value:
                    return None
                bindings[name] = value
            elif isinstance(v, Variable):
                if theta.get(v, q) != q:
                    return None
                theta[v] = q
            elif v != q:
                return None
        return theta, bindings

    def rec(i, theta, bindings, used):
        if i == n:
            yield theta, bindings, tuple(sorted(used))
            return
        for j, qt in enumerate(query_triples):
            if j in used:
                continue
            r = unify(view_triples[i], qt, theta, bindings)
            if r is not None:
                yield from rec(i + 1, r[0], r[1], used | {j})

    yield from rec(0, {}, {}, frozenset())


def _filter_vars(e, out):
    if isinstance(e, Variable):
        out.add(e)
    elif isinstance(e, tuple):
        for x in e[1:]:
            _filter_vars(x, out)
    return out


def needed_vars(q):
    out = set(v for v in q.projection if v in set(q.variables()))
    out |= set(q.group_by)
    out |= {a.var for a in q.aggregates if a.var is not None}
    for f in q.filters:
        _filter_vars(f, out)
    out |= {v for v, _ in q.order_by if v in set(q.variables())}
    return out


def is_row_view(t):
    ir = t.ir
    return (ir is not None and ir.form == "SELECT" and not ir.aggregates and not ir.group_by
            and not ir.filters and not ir.distinct and ir.limit is None)


def mcds(q, templates):
    """MiniCon descriptions of ``q`` for the row templates."""
    need = needed_vars(q)
    out = []
    for t in templates:
        if not is_row_view(t):
            continue
        head = set(t.ir.projection)
        seen = set()
        for theta, bindings, covered in _embeddings(t.ir.triples, q.triples, t.slots):
            cov = set(covered)
            outside = {x for j, tr in enumerate(q.triples) if j not in cov for x in tr
                       if isinstance(x, Variable)}
            ok, columns, selections, equalities = True, {}, [], []
            for v, qterm in theta.items():
                exported = v in head
                if not isinstance(qterm, Variable):
                    if not exported:
                        ok = False
                        break
                    selections.append((v, qterm))
                    continue
                if (qterm in need or qterm in outside) and not exported:
                    # MiniCon property 1 fails unless another exported variable maps there
                    if not any(w in head and theta[w] == qterm for w in theta):
                        ok = False
                        break
                    continue
                if not exported:
                    continue
                if qterm in columns.values():
                    equalities.append((v, qterm))
                else:
                    columns[v] = qterm
            if not ok:
                continue
            # every needed or frontier variable of the covered atoms must be returned
            covered_vars = {x for j in cov for x in q.triples[j] if isinstance(x, Variable)}
            if any(x not in columns.values() for x in covered_vars if x in need or x in outside):
                continue
            sig = (covered, tuple(sorted(bindings.items())), tuple(sorted((str(k), str(v))
                                                                       for k, v in theta.items())))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(Call(t, bindings, columns, selections, equalities, covered))
    return out


def combine(q, descriptions, limit=200000):
    """Best set of MCDs covering all atoms disjointly: (level, #templates, #triples) minimal."""
    n = len(q.triples)
    by_atom = {i: [m for m in descriptions if i in m.covered] for i in range(n)}
    best = [None]
    steps = [0]

    def cost(ms):
        return (max(m.template.level for m in ms), len(ms), sum(len(m.covered) for m in ms))

    def rec(covered, chosen):
        steps[0] += 1
        if steps[0] > limit:
            return
        if len(covered) == n:
            if best[0] is None or cost(chosen) < cost(best[0]):
                best[0] = list(chosen)
            return
        if best[0] is not None and cost(chosen)[0] > cost(best[0])[0]:
            return
        i = min((i for i in range(n) if i not in covered), key=lambda i: len(by_atom[i]))
        for m in by_atom[i]:
            if covered & set(m.covered):
                continue
            chosen.append(m)
            rec(covered | set(m.covered), chosen)
            chosen.pop()

    rec(frozenset(), [])
    return best[0]


# --- whole match ------------------------------------------------------------

def _flatten_and(fs):
    out = []
    for f in fs:
        if isinstance(f, tuple) and f[0] == "and":
            out += _flatten_and([f[1], f[2]])
        else:
            out.append(f)
    return out


def _map_expr(e, theta, bindings, slots):
    """Template filter expression with variables renamed and parameters bound, or None."""
    if _is_param(e):
        return ("param", str(e)[len(PARAM_PREFIX):])
    if isinstance(e, Variable):
        return theta.get(e, e)
    if isinstance(e, tuple):
        return (e[0],) + tuple(x if isinstance(x, str) else _map_expr(x, theta, bindings, slots)
                               for x in e[1:])
    return e


def _expr_equal(t_expr, q_expr, bindings, slots):
    if isinstance(t_expr, tuple) and t_expr and t_expr[0] == "param":
        name = t_expr[1]
        if isinstance(q_expr, Variable) or isinstance(q_expr, tuple):
            return False
        value = slots[name].value_for(q_expr)
        if value is None or bindings.get(name, value) != value:
            return False
        bindings[name] = value
        return True
    if isinstance(t_expr, tuple) and isinstance(q_expr, tuple):
        return (len(t_expr) == len(q_expr) and t_expr[0] == q_expr[0]
                and all(a == b if isinstance(a, str) else _expr_equal(a, b, bindings, slots)
                        for a, b in zip(t_expr[1:], q_expr[1:])))
    return t_expr == q_expr


def match_whole(t, q):
    """Plan running template ``t`` as it is, if ``q`` is one of its instances."""
    ti = t.ir
    if (ti is None or ti.form != q.form or len(ti.triples) != len(q.triples)
            or len(ti.aggregates) != len(q.aggregates) or ti.distinct != q.distinct
            or len(ti.group_by) != len(q.group_by) or ti.limit is not None):
        return None
    t_filters, q_filters = _flatten_and(ti.filters), _flatten_and(q.filters)
    if len(t_filters) != len(q_filters):
        return None
    for theta, bindings, _ in _embeddings(ti.triples, q.triples, t.slots):
        if not all(isinstance(x, Variable) for x in theta.values()):
            continue
        if len(set(theta.values())) != len(theta):
            continue   # variable renaming must be a bijection
        b = dict(bindings)
        mapped = [_map_expr(f, theta, b, t.slots) for f in t_filters]
        remaining = list(q_filters)
        ok = True
        for mf in mapped:
            hit = next((i for i, qf in enumerate(remaining) if _expr_equal(mf, qf, b, t.slots)), None)
            if hit is None:
                ok = False
                break
            remaining.pop(hit)
        if not ok or set(b) != set(t.slots):
            continue
        if {theta.get(v) for v in ti.group_by} != set(q.group_by):
            continue
        aliases, used = {}, set()
        for ta in ti.aggregates:
            qa = next((a for a in q.aggregates if a not in used and a.fn == ta.fn
                       and a.distinct == ta.distinct
                       and (a.var is None if ta.var is None else theta.get(ta.var) == a.var)), None)
            if qa is None:
                ok = False
                break
            used.add(qa)
            aliases[ta.alias] = qa.alias
        if not ok:
            continue
        for v in ti.projection:
            aliases.setdefault(v, theta.get(v, v))
        if [aliases.get(v) for v in ti.projection] != list(q.projection) and \
                set(aliases.get(v) for v in ti.projection) != set(q.projection):
            continue
        return Plan("whole", [Call(t, b, {}, covered=tuple(range(len(q.triples))))], q, aliases)
    return None


# --- entry point ------------------------------------------------------------

def rewrite(q, templates):
    """Best plan for ``q``; NoRewriting with the reasons otherwise."""
    candidates = [p for p in (match_whole(t, q) for t in templates) if p]
    descriptions = mcds(q, templates)
    best = combine(q, descriptions)
    if best:
        candidates.append(Plan("rewrite", best, q))
    if candidates:
        return min(candidates, key=lambda p: (p.level, len(p.calls), p.kind != "whole"))
    covered = {i for m in descriptions for i in m.covered}
    reasons = []
    for i, tr in enumerate(q.triples):
        if i not in covered:
            reasons.append("no catalog template covers the pattern  "
                           + " ".join(render_term(x) for x in tr))
    if not reasons:
        reasons.append("the templates covering the pattern cannot be combined without "
                       "overlapping or losing a variable the query needs")
    raise NoRewriting(reasons)
