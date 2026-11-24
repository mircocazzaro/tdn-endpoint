"""Autorizzazione di una query Compose: riscrittura sul catalogo attivo.

La madre che non coincide con un template e' ammessa se esiste una
riscrittura equivalente nel frammento supportato (stessa politica di Central:
si sceglie il piano di livello piu' basso, poi con meno template). Il livello
della madre e' il massimo dei livelli dei template del piano scelto, calcolato
sul catalogo attivo di questo endpoint.
"""
import threading

from . import admission, ir, minicon

_cache = {"digest": None, "templates": []}
_lock = threading.Lock()


def templates_of(document):
    """Template analizzati di un documento di catalogo (``Catalog.to_document()``)."""
    out = []
    for e in document.get("templates", []):
        try:
            out.append(ir.parse_template(e, document.get("prologue") or ir.PROLOGUE))
        except ir.Unsupported:
            continue   # non analizzabile: non puo' far parte di una riscrittura
    return out


def active_templates(catalog):
    """Template analizzati del catalogo ``catalog`` (in cache finche' non cambia)."""
    digest = catalog.content_digest()
    with _lock:
        if _cache["digest"] != digest:
            _cache.update(digest=digest, templates=templates_of(catalog.to_document()))
        return _cache["templates"]


def plan(sparql, templates, prologue=ir.PROLOGUE):
    """Piano di riscrittura; solleva ``ir.Unsupported`` o ``minicon.NoRewriting``."""
    q = ir.parse_query(sparql, prologue)
    p = minicon.rewrite(q, templates)
    ok, reason = admission.admissible(p)
    if not ok:
        raise minicon.NoRewriting([reason])
    return p


def level_of(sparql, catalog):
    """Livello della madre composta ``sparql`` sul catalogo ``catalog``.

    Solleva ``ir.Unsupported`` (fuori dal frammento) o ``minicon.NoRewriting``
    (nessuna riscrittura): in entrambi i casi la madre non e' ammessa.
    """
    p = plan(sparql, active_templates(catalog), catalog.prologue)
    return admission.plan_level(p), p
