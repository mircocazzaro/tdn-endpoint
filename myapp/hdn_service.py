"""Servizio hdn-components v1 dell'endpoint: contesto, autorizzazione, esecuzione.

Il flusso di una richiesta (``respond``) e':

1. il piano si calcola dal testo della madre e dai metadati condivisi
   (catalogo, ontologia, profilo), prima di ogni controllo: cosi' rifiuto,
   assenza di match e guasto producono la stessa struttura pubblica;
2. la madre si autorizza: istanza di template (``catalog.match_query``) oppure,
   se non lo e', riscrittura sul catalogo attivo (``rewriting.service``); il
   livello deve stare entro quello configurato;
3. superato il controllo, tutte le componenti derivate sono autorizzate
   (decisione sperimentale del progetto) e girano sul backend attivo;
4. lo stato locale (dati, mapping, ontologia, catalogo, sorgente) deve essere
   lo stesso prima e dopo l'esecuzione, altrimenti niente contributo.

Ogni uscita negativa e' ``empty_envelope`` con lo stesso piano; i motivi vanno
solo nei log locali (``Outcome.reason``).
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog as catalog_mod
from . import hdn_components as hc
from . import hdn_planner

log = logging.getLogger("hdn.components")

DEFAULT_MAX_ROWS = 200_000


class NotAuthorized(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason, self.details = reason, details


class ExecutionFailed(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.reason, self.details = reason, details


@dataclass
class Authorization:
    level: int
    kind: str                      # 'template' | 'compose'
    key: str = ""                  # chiave del template (o dei template del piano)
    match: object = None           # QueryMatch per i template
    alternatives: tuple = ()


@dataclass
class Outcome:
    envelope: dict
    contributed: bool
    reason: str = ""
    details: dict = field(default_factory=dict)
    authorization: object = None


# --- contesto ----------------------------------------------------------------

def _file_sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return hc.sha256_text("")
    return h.hexdigest()


def build_context(catalog, profile, ontology_path):
    return hc.context(catalog.content_digest(), _file_sha256(ontology_path), profile.sha256)


def state_stamp(state_dir, extra_files=()):
    """Impronta dello stato locale che cambia il risultato di una componente.

    Dimensione e mtime dei file di stato (sorgenti dati, catalogo, ontologia,
    profilo) e dei file ``extra_files`` (database DuckDB e relativo WAL, mapping).
    Due letture uguali prima e dopo l'esecuzione escludono una modifica
    concorrente fra le componenti. Con un triple store esterno i dati non sono
    sotto controllo dell'endpoint: la verifica copre solo la configurazione.
    """
    state = Path(state_dir)
    paths = [state / "datasources.json", state / "catalog.json", state / "profile.json"]
    for sub in ("sources", "ontology"):
        d = state / sub
        if d.is_dir():
            paths += sorted(p for p in d.rglob("*") if p.is_file())
    paths += [Path(p) for p in extra_files]
    paths += [Path(str(p) + ".wal") for p in extra_files]
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


# --- autorizzazione ----------------------------------------------------------

def authorize(q, cat):
    """Autorizzazione della madre ``q`` sul catalogo ``cat``.

    Prima l'istanza di template, con le precedenze attuali; se la query non lo
    e' (anche solo per i prefissi del form Compose) la riscrittura sul
    catalogo. Solleva ``NotAuthorized``.
    """
    from .rewriting import ir as rw_ir, minicon, service

    template_reason = None
    try:
        m = catalog_mod.match_query(q, cat)
        return Authorization(m.template.level, "template", m.template.key, m, m.alternatives)
    except catalog_mod.RejectedQuery as exc:
        if exc.reason == "query-too-large":
            raise NotAuthorized(exc.reason, **exc.details)
        template_reason = exc.reason
    try:
        level, plan = service.level_of(q, cat)
    except rw_ir.Unsupported as exc:
        raise NotAuthorized(template_reason, compose=f"unsupported: {exc}")
    except minicon.NoRewriting as exc:
        raise NotAuthorized(template_reason, compose="no-rewriting")
    keys = ",".join(sorted({c.template.key for c in plan.calls}))
    return Authorization(level, "compose", keys)


# --- piano -------------------------------------------------------------------

def plan_for(q, cat, profile):
    """``(evaluation_query, plan)`` o ``(q, None)`` se non pianificabile."""
    text = q.strip()
    if len(text) > catalog_mod.MAX_QUERY_CHARS:
        return text, None
    try:
        return text, hdn_planner.plan_query(text, profile)
    except hc.Unplannable as first:
        # Una madre senza prologo (ammessa dal match dei template) si valuta
        # con il prologo del catalogo; il testo di valutazione lo dichiara.
        prefixed = cat.prologue + text
        try:
            return prefixed, hdn_planner.plan_query(prefixed, profile)
        except hc.Unplannable:
            log.info("unplannable reason=%s", first)
            return text, None


# --- esecuzione --------------------------------------------------------------

def execute(plan, run, timeout, max_rows=DEFAULT_MAX_ROWS):
    """Esegue le componenti con ``run(query, timeout)``; restituisce (pool, memberships).

    Un guasto o un limite di righe sollevano ``ExecutionFailed``: nessun
    contributo parziale e nessun troncamento silenzioso.
    """
    pool = hc.PoolBuilder()
    total = 0
    for comp in plan["components"]:
        query = hdn_planner.component_query(plan, comp)
        try:
            data = run(query, timeout)
        except Exception as exc:
            raise ExecutionFailed("backend-error", error=type(exc).__name__, component=comp["id"])
        try:
            bindings = data["results"]["bindings"]
        except (KeyError, TypeError):
            raise ExecutionFailed("backend-malformed", component=comp["id"])
        if not comp["vars"]:
            try:
                n = int(bindings[0]["n"]["value"]) if bindings else 0
            except (KeyError, ValueError, TypeError, IndexError):
                raise ExecutionFailed("backend-malformed", component=comp["id"])
            if n > 0:
                pool.add(comp["id"], [], [], n)
            continue
        total += len(bindings)
        if total > max_rows:
            raise ExecutionFailed("row-limit", rows=total, limit=max_rows)
        cols = list(range(len(comp["vars"])))
        for b in bindings:
            try:
                vec = [hc.normalize_result_term(b.get(v)) for v in comp["vars"]]
            except (ValueError, KeyError, AttributeError):
                raise ExecutionFailed("backend-malformed", component=comp["id"])
            if any(t is not None and t["type"] == "bnode" for t in vec):
                # Le etichette dei blank node hanno scope di risposta: non si
                # possono usare per unire componenti di chiamate diverse.
                raise ExecutionFailed("blank-node", component=comp["id"])
            pool.add(comp["id"], cols, vec, 1)
    return pool.pool, pool.memberships()


def respond(q, *, cat, profile, ontology_path, local_max_level, run, timeout,
            stamp, prepare=None, max_rows=DEFAULT_MAX_ROWS):
    """Risposta v1 per la madre ``q``. Non solleva: ogni esito negativo e' canonico.

    ``stamp()`` restituisce l'impronta dello stato locale; ``prepare(auth)``,
    se dato, esegue il lavoro preliminare richiesto dalla madre (Galois) e puo'
    sollevare ``NotAuthorized`` o ``ExecutionFailed``.
    """
    ctx = build_context(cat, profile, ontology_path)
    evaluation_query, plan = plan_for(q, cat, profile)

    def empty(reason, auth=None, **details):
        return Outcome(hc.empty_envelope(q, ctx, plan), False, reason, details, auth)

    auth = None
    try:
        auth = authorize(q, cat)
        if auth.level > local_max_level:
            return empty("disclosure-refused", auth, requested=auth.level, local_max=local_max_level)
        if plan is None:
            return empty("unplannable", auth)
        if prepare is not None:
            prepare(auth)
        before = stamp()
        pool, memberships = execute(plan, run, timeout, max_rows)
        if stamp() != before:
            return empty("state-changed", auth)
    except NotAuthorized as exc:
        return empty(exc.reason, auth, **exc.details)
    except ExecutionFailed as exc:
        return empty(exc.reason, auth, **exc.details)
    except Exception as exc:   # difesa: nessuna eccezione deve distinguersi da un guasto
        log.exception("component-execution-failed")
        return empty("internal-error", auth, error=type(exc).__name__)
    return Outcome(hc.envelope(q, ctx, plan, pool, memberships), True, "", {}, auth)
