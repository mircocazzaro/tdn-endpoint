"""Planner dinamico: partizione degli atomi di ogni BGP in componenti.

Segue il modello di "HDN and OBDF" (hdn theory.pdf, sez. 3, 4.2, 5.2, 6.1,
6.4), con il *profilo di federazione* Phi:

- ``reference_predicates`` (Pi_ref): predicati il cui livello di fatti e'
  replicato in ogni sito. Un atomo con quel predicato e' un *atomo di
  riferimento*: non appartiene a nessuna componente di sito, si valuta sul
  livello replicato e le copie dei siti si fondono (``global_witness_set``);
- ``local_positions`` (Lambda): coppie (predicato, indice) in cui compaiono
  costanti locali a un sito; indice 1 = soggetto, 2 = oggetto. Una variabile
  e' *ancorata* se occupa una posizione locale in almeno un atomo;
- ``site_prefixes``: prefissi degli IRI di ciascun sito, per riconoscere il
  proprietario di una costante locale (senza prefissi ogni costante locale
  e' proprietaria di se stessa).

Grafo di ancoraggio (def. 4.2): due atomi non di riferimento sono adiacenti se
condividono una variabile ancorata, o se contengono costanti locali dello
stesso proprietario. Le componenti connesse sono le componenti del piano; un
atomo senza variabili ancorate ne' costanti locali forma una componente da
solo. Variabili condivise ma non ancorate (codici, farmaci, eta') non legano:
le componenti si uniscono al centro, anche fra siti diversi.

Merge mode: una componente con una variabile ancorata o una costante locale
ha soluzioni di un solo sito (prop. 6.1): ``site_unique_bag``, molteplicita'
sommate fra siti, e si esportano solo le variabili di interfaccia con i
conteggi (eager counting, prop. 6.4). Le altre (riferimento e atomi senza
ancoraggio) sono ``global_witness_set`` con tutte le variabili: la stessa
soluzione puo' esistere in piu' siti e va contata una volta.

Il profilo si legge da ``HDN_STATE_DIR/profile.json``; senza file vale il
default, che dichiara locale il soggetto di ogni predicato e nessun predicato
di riferimento (estensione: ``"predicate": "*"`` vale per ogni predicato).
Esempio::

    {"version": 1,
     "reference_predicates": ["https://example.org/broader"],
     "local_positions": [{"predicate": "https://example.org/takes", "index": 1}],
     "site_prefixes": [{"site": 1, "prefix": "https://example.org/site/A/"}]}

Il digest del profilo e' nel ``context`` della risposta.
"""

import json
import logging
from pathlib import Path

from . import hdn_components as hc

log = logging.getLogger("hdn.planner")

DEFAULT_PROFILE = {
    "version": 1,
    "reference_predicates": [],
    "local_positions": [{"predicate": "*", "index": 1}],
    "site_prefixes": [],
}


class Profile:
    def __init__(self, document=None):
        doc = {**DEFAULT_PROFILE, **(document or {})}
        self.document = doc
        self.reference = set(doc["reference_predicates"])
        self._local = set()
        for r in doc["local_positions"]:
            if r.get("index") not in (1, 2) or not isinstance(r.get("predicate"), str):
                raise ValueError(f"posizione locale non valida: {r!r}")
            self._local.add((r["predicate"], r["index"]))
        self._prefixes = sorted(((p["prefix"], p["site"]) for p in doc["site_prefixes"]),
                                key=lambda x: -len(x[0]))

    def is_reference(self, predicate):
        return predicate in self.reference

    def is_local(self, predicate, index):
        """``predicate``: IRI, o None per un predicato variabile (vale solo ``*``)."""
        return (predicate is not None and (predicate, index) in self._local) or ("*", index) in self._local

    def owner(self, iri):
        for prefix, site in self._prefixes:
            if iri.startswith(prefix):
                return ("site", site)
        return ("iri", iri)

    @property
    def sha256(self):
        return hc.sha256_text(hc.canonical_json(self.document))


def load_profile(state_dir):
    path = Path(state_dir) / "profile.json"
    try:
        return Profile(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return Profile()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Un profilo illeggibile non si sostituisce in silenzio con quello di
        # default: cambierebbe le garanzie. Si registra e si usa il default,
        # il cui digest differente rende il cambio visibile a Central.
        log.error("profile-unreadable path=%s error=%s: using default", path, type(exc).__name__)
        return Profile()


def _pred(atom):
    p = atom["pattern"][1]
    return p["value"] if p["type"] == "uri" else None


def _anchoring(atom, profile):
    """(variabili ancorate, proprietari delle costanti locali) di un atomo."""
    s, _, o = atom["pattern"]
    pred = _pred(atom)
    anchored, owners = set(), set()
    for term, index in ((s, 1), (o, 2)):
        if not profile.is_local(pred, index):
            continue
        if term["type"] == "var":
            anchored.add(term["value"])
        elif term["type"] == "uri":
            owners.add(profile.owner(term["value"]))
        else:
            owners.add(("literal", term["value"]))
    return anchored, owners


class _Groups:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def groups(self, members):
        out = {}
        for i in members:
            out.setdefault(self.find(i), []).append(i)
        return list(out.values())


def partition_for(profile):
    """Funzione ``partition(bgp_id, atoms)`` per ``hdn_components.build_plan``."""

    def partition(_bgp_id, atoms):
        reference = [i for i, a in enumerate(atoms) if profile.is_reference(_pred(a))]
        regular = [i for i in range(len(atoms)) if i not in reference]
        info = {i: _anchoring(atoms[i], profile) for i in range(len(atoms))}
        # Variabile ancorata: occupa una posizione locale in almeno un atomo.
        anchored = set().union(*(info[i][0] for i in range(len(atoms)))) if atoms else set()

        g = _Groups(len(atoms))
        seen_var, seen_owner = {}, {}
        for i in regular:
            for v in hc.atom_vars(atoms[i]["pattern"]):
                if v in anchored:
                    g.union(i, seen_var.setdefault(v, i))
            for o in info[i][1]:
                g.union(i, seen_owner.setdefault(o, i))
        out = []
        for idx in g.groups(regular):
            has_anchor = any(info[i][0] or info[i][1] for i in idx)
            out.append((idx, "site_unique_bag", True) if has_anchor
                       else (idx, "global_witness_set", False))

        # Atomi di riferimento: valutati sul livello replicato; si raggruppano
        # per variabili comuni e si fondono per testimone.
        r = _Groups(len(atoms))
        first = {}
        for i in reference:
            for v in hc.atom_vars(atoms[i]["pattern"]):
                r.union(i, first.setdefault(v, i))
        out += [(idx, "global_witness_set", False) for idx in r.groups(reference)]
        return out

    return partition


def plan_query(evaluation_query, profile):
    """Piano v1 della query; ``Unplannable`` se fuori dal frammento."""
    return hc.build_plan(evaluation_query, partition_for(profile))


def component_query(plan, component):
    """SELECT locale di una componente, costruita dagli atomi del piano.

    I termini sono ricostruiti dalla loro codifica e riscritti da RDFLib: nulla
    del testo della madre arriva al backend. La SELECT non ha DISTINCT, quindi
    ogni riga e' una soluzione e il conteggio dei binding uguali da' la
    molteplicita'. Senza variabili esportate si conta il numero di soluzioni
    (un valore booleano/molteplicita' della soluzione unitaria).
    """
    atoms = {a["id"]: a for b in plan["bgps"] if b["id"] == component["bgp_id"] for a in b["atoms"]}
    lines = []
    for aid in component["atom_ids"]:
        lines.append("  " + " ".join(hc.decode_term(t).n3() for t in atoms[aid]["pattern"]) + " .")
    body = "WHERE {\n" + "\n".join(lines) + "\n}"
    if component["vars"]:
        return "SELECT " + " ".join("?" + v for v in component["vars"]) + " " + body
    return "SELECT (COUNT(*) AS ?n) " + body
