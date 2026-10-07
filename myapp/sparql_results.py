"""Shaping delle risposte del protocollo SPARQL per l'HDN Endpoint.

Il protocollo HDN (D3.2, sez. 2.1.1, 2.2.3, 2.4.4) richiede che ogni mancato
contributo di un endpoint sia osservazionalmente identico, qualunque ne sia la
causa: assenza di dati che matchano, rifiuto per livello di disclosure,
template non riconosciuto, fallimento di autorizzazione, errore di trasporto o
timeout. HDN Central deve poter osservare soltanto "questo endpoint non ha
contribuito", mai il perche'.

Questo modulo costruisce la risposta vuota canonica per una data query,
rispettando la forma della query, in modo che sia confrontabile byte per byte
con una risposta genuinamente vuota prodotta da Ontop.
"""

import re

_PREFIX_RE = re.compile(
    r"^\s*(?:PREFIX\s+[^:\s]*:\s*<[^>]*>|BASE\s*<[^>]*>)\s*", re.IGNORECASE
)
_FORM_RE = re.compile(r"\b(ASK|SELECT|CONSTRUCT|DESCRIBE)\b", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_MODIFIER_RE = re.compile(r"\s*(DISTINCT|REDUCED)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_VAR_RE = re.compile(r"[?$]([A-Za-z0-9_]+)")
_AS_ALIAS_RE = re.compile(r"\bAS\s+[?$]([A-Za-z0-9_]+)", re.IGNORECASE)


def strip_comments(query):
    """Rimuove i commenti '#' lasciando intatti letterali stringa e IRI."""
    out = []
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if c in "'\"":
            quote = c
            out.append(c)
            i += 1
            while i < n:
                if query[i] == "\\" and i + 1 < n:
                    out.append(query[i:i + 2])
                    i += 2
                    continue
                out.append(query[i])
                if query[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "<":
            j = query.find(">", i)
            if j != -1 and "\n" not in query[i:j]:
                out.append(query[i:j + 1])
                i = j + 1
                continue
        if c == "#":
            j = query.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _body(query):
    """Query senza commenti e senza le dichiarazioni PREFIX/BASE iniziali."""
    s = strip_comments(query)
    while True:
        m = _PREFIX_RE.match(s)
        if not m:
            break
        s = s[m.end():]
    return s


def query_form(query):
    """'ASK', 'SELECT', 'CONSTRUCT', 'DESCRIBE' oppure 'UNKNOWN'."""
    m = _FORM_RE.search(_body(query))
    return m.group(1).upper() if m else "UNKNOWN"


def projected_vars(query):
    """Variabili proiettate da una SELECT, nell'ordine di dichiarazione.

    Gestisce le proiezioni con espressione (``(AVG(?x) AS ?y)``), i modificatori
    DISTINCT/REDUCED e ``SELECT *``; per ``SELECT *`` riporta tutte le variabili
    menzionate nella query, che e' l'approssimazione piu' vicina a cio' che
    restituirebbe il motore.
    """
    s = _body(query)
    m = _SELECT_RE.search(s)
    if not m:
        return []

    i = m.end()
    while True:
        mod = _MODIFIER_RE.match(s, i)
        if not mod:
            break
        i = mod.end()

    names = []
    star = False
    j, n = i, len(s)
    while j < n:
        c = s[j]

        if c == "{":
            break
        where = _WHERE_RE.match(s, j)
        if where and (where.end() >= n or not s[where.end()].isalnum()):
            break

        if c == "(":
            depth, k = 0, j
            while k < n:
                if s[k] == "(":
                    depth += 1
                elif s[k] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            inner = s[j:k + 1]
            alias = _AS_ALIAS_RE.search(inner)
            if alias:
                names.append(alias.group(1))
            j = k + 1
            continue

        if c in "?$":
            var = _VAR_RE.match(s, j)
            if var:
                names.append(var.group(1))
                j = var.end()
                continue

        if c == "*":
            star = True

        j += 1

    if star and not names:
        names = _VAR_RE.findall(s)

    return list(dict.fromkeys(names))


def empty_result(query):
    """Risposta vuota canonica, nella forma SPARQL-JSON attesa per ``query``.

    E' l'unica risposta che l'endpoint restituisce per qualunque mancato
    contributo, cosi' che rifiuti, template ignoti, errori di backend e assenza
    di dati siano indistinguibili dall'esterno.
    """
    if query_form(query) == "ASK":
        return {"head": {}, "boolean": False}
    return {"head": {"vars": projected_vars(query)}, "results": {"bindings": []}}
