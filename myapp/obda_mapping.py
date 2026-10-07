"""Parsing del formato di mapping nativo di Ontop (file ``.obda``).

Il formato e' una lista di blocchi dentro ``[MappingDeclaration] @collection [[ ... ]]``,
ciascuno composto da tre campi:

    mappingId   <identificatore>
    target      <triple pattern con segnaposto {colonna}>
    source      <query SQL sulla sorgente locale>

Il modulo espone un parser unico, cosi' che la UI di mapping, la generazione
del file e i test lavorino sulla stessa rappresentazione invece di ripetere
espressioni regolari leggermente diverse.
"""

import re

_TARGET_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
_TYPED_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}\s*\^\^\s*([\w:]+)")
_FROM_TABLE_RE = re.compile(r'FROM\s+"([^"]+)"', re.IGNORECASE)


class MappingBlock:
    """Un singolo blocco mappingId/target/source."""

    __slots__ = ("mapping_id", "target", "source")

    def __init__(self, mapping_id, target, source):
        self.mapping_id = mapping_id
        self.target = target
        self.source = source

    @property
    def placeholders(self):
        """Segnaposto ``{colonna}`` citati nel target, in ordine, senza duplicati."""
        return list(dict.fromkeys(_TARGET_PLACEHOLDER_RE.findall(self.target)))

    @property
    def typed_placeholders(self):
        """Coppie (segnaposto, datatype) per i segnaposto con ``^^`` nel target."""
        return [(m.group(1), m.group(2))
                for m in _TYPED_PLACEHOLDER_RE.finditer(self.target)]

    @property
    def source_table(self):
        """Prima tabella citata da una FROM "..." nel source, se presente."""
        m = _FROM_TABLE_RE.search(self.source)
        return m.group(1) if m else None

    def __repr__(self):
        return f"<MappingBlock {self.mapping_id}>"


def split_collection(text):
    """Restituisce (header, corpo) dove corpo e' l'interno di ``@collection [[ ]]``.

    Solleva ValueError se il file non ha la struttura attesa, invece di
    propagare un AttributeError da un ``.group()`` su None.
    """
    if "[MappingDeclaration]" not in text:
        raise ValueError("file .obda senza sezione [MappingDeclaration]")
    header, rest = text.split("[MappingDeclaration]", 1)
    m = re.search(r"@collection\s*\[\[(.*)\]\]", rest, re.S)
    if not m:
        raise ValueError("sezione [MappingDeclaration] senza @collection [[ ... ]]")
    return header, m.group(1)


def parse_mappings(text):
    """Estrae i blocchi di mapping da un file ``.obda`` completo."""
    _, body = split_collection(text)
    blocks = []
    for chunk in re.split(r"\n\s*\n", body.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        mid = re.search(r"mappingId\s+(\S+)", chunk)
        tgt = re.search(r"target\s+(.*?)\n\s*source\b", chunk, re.S)
        src = re.search(r"source\s+(.*)", chunk, re.S)
        if not (mid and tgt and src):
            continue
        # source e target sono conservati cosi' come sono scritti: il
        # rientro e i newline fanno parte del testo su cui opera la
        # sostituzione, e normalizzarli qui nasconderebbe proprio i casi in
        # cui un segnaposto chiude una riga.
        blocks.append(MappingBlock(
            mapping_id=mid.group(1),
            target=tgt.group(1).strip(),
            source=src.group(1).strip(),
        ))
    return blocks


# Tokenizzatore minimale: distingue i literal fra apici singoli e gli
# identificatori fra doppi apici dagli identificatori nudi, cosi' che una
# rinomina non entri mai dentro una stringa o dentro un nome di tabella
# quotato (es. FROM "PATIENTS GENERAL DATA").
_SQL_TOKEN_RE = re.compile(
    r"'(?:[^']|'')*'"       # literal fra apici singoli
    r'|"(?:[^"]|"")*"'      # identificatore fra doppi apici
    r"|[A-Za-z_]\w*"        # identificatore nudo
)


def _case_insensitive(renames):
    return {k.lower(): v for k, v in renames.items()}


def substitute_identifiers(sql, renames):
    """Rinomina identificatori in una query SQL.

    La sostituzione e' simultanea: ogni token viene esaminato una volta sola e
    riscritto al massimo una volta. Applicare le rinomine in sequenza con
    ``str.replace`` e' sbagliato in due modi, entrambi presenti nella versione
    precedente del generatore:

    - una rinomina ``a -> b`` seguita da ``b -> c`` trasforma il primo token in
      ``c``, propagando a cascata;
    - ancorare la sostituzione a ``var+','``, ``var+' '``, ``var+')'`` e
      ``'('+var`` non copre il caso in cui il segnaposto sia l'ultimo token di
      una riga, perche' nessuna delle quattro ancore contiene il newline. Nei
      template i source vanno a capo, percio' un'occorrenza in SELECT restava
      invariata mentre quella in WHERE veniva riscritta, producendo un mapping
      incoerente.
    """
    if not renames:
        return sql
    lowered = _case_insensitive(renames)

    def _replace(match):
        token = match.group(0)
        if token[0] in "'\"":
            return token
        if token in renames:
            return renames[token]
        return lowered.get(token.lower(), token)

    return _SQL_TOKEN_RE.sub(_replace, sql)


def substitute_target_placeholders(target, renames):
    """Rinomina i segnaposto ``{x}`` di un target, in un solo passaggio."""
    if not renames:
        return target
    lowered = _case_insensitive(renames)

    def _replace(match):
        name = match.group(1)
        if name in renames:
            return "{" + renames[name] + "}"
        return "{" + lowered.get(name.lower(), name) + "}"

    return _TARGET_PLACEHOLDER_RE.sub(_replace, target)


def source_identifiers(sql):
    """Identificatori citati da una query SQL, in minuscolo.

    Include sia quelli nudi sia quelli fra doppi apici, privati degli apici.
    """
    names = set()
    for token in _SQL_TOKEN_RE.findall(sql):
        if token[0] == '"':
            names.add(token[1:-1].lower())
        elif token[0] != "'":
            names.add(token.lower())
    return names


_SELECT_KW_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_PROJECTION_MODIFIER_RE = re.compile(r"\s*(DISTINCT|ALL)\b", re.IGNORECASE)
_FROM_KW_RE = re.compile(r"\bFROM\b", re.IGNORECASE)
_AS_TAIL_RE = re.compile(r"\bAS\s+(\"[^\"]+\"|[A-Za-z_]\w*)\s*$", re.IGNORECASE)
_PLAIN_COLUMN_RE = re.compile(
    r"^(?:(?:\"[^\"]+\"|[A-Za-z_]\w*)\s*\.\s*)?(\"[^\"]+\"|[A-Za-z_]\w*)$"
)


def _split_top_level(text, separator=","):
    """Divide ``text`` sul separatore, ignorando quelli dentro parentesi o
    dentro literal e identificatori quotati."""
    parts, buf, depth = [], [], 0
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            quote = c
            j = i + 1
            while j < n:
                if text[j] == quote:
                    if j + 1 < n and text[j + 1] == quote:
                        j += 2
                        continue
                    break
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == separator and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _projection_span(sql):
    """(inizio, fine) della lista di proiezione di primo livello, o None."""
    m = _SELECT_KW_RE.search(sql)
    if not m:
        return None

    i = m.end()
    mod = _PROJECTION_MODIFIER_RE.match(sql, i)
    if mod:
        i = mod.end()

    # Fine della proiezione: la prima FROM a profondita' zero.
    depth, end = 0, len(sql)
    j = i
    while j < len(sql):
        c = sql[j]
        if c in "'\"":
            quote = c
            k = j + 1
            while k < len(sql):
                if sql[k] == quote:
                    if k + 1 < len(sql) and sql[k + 1] == quote:
                        k += 2
                        continue
                    break
                k += 1
            j = k + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            kw = _FROM_KW_RE.match(sql, j)
            if kw:
                end = j
                break
        j += 1
    return i, end


def projected_columns(sql):
    """Nomi delle colonne che una query SELECT produce, piu' un flag wildcard.

    Restituisce ``(nomi_minuscoli, ha_wildcard)``. Considera solo la proiezione
    di primo livello: una colonna citata soltanto in una WHERE o in una
    sottoquery non viene prodotta dalla query e non puo' soddisfare un
    segnaposto del target.

    Con ``SELECT *`` l'insieme non e' determinabile offline e il flag wildcard
    segnala che non si possono trarre conclusioni.
    """
    span = _projection_span(sql)
    if span is None:
        return set(), True
    i, end = span

    names, wildcard = set(), False
    for item in _split_top_level(sql[i:end]):
        if item == "*" or item.endswith(".*"):
            wildcard = True
            continue
        alias = _AS_TAIL_RE.search(item)
        if alias:
            names.add(alias.group(1).strip('"').lower())
            continue
        plain = _PLAIN_COLUMN_RE.match(item)
        if plain:
            names.add(plain.group(1).strip('"').lower())
            continue
        # Espressione senza alias: il nome della colonna risultante dipende dal
        # motore, quindi non e' verificabile offline.
        wildcard = True

    return names, wildcard


def unresolved_placeholders(target, source):
    """Segnaposto del target che il source non produce come colonna.

    Un target che proietta ``{X}`` mentre il source non espone una colonna
    ``X`` genera un mapping che Ontop non puo' soddisfare. Controllare questa
    condizione prima di scrivere il file e' cio' che impedisce al generatore di
    corrompere in silenzio il contratto semantico con HERO.

    Nota: non basta che ``X`` sia citata nel source. Nella versione precedente
    del generatore una rinomina incompleta lasciava il nome nuovo nella sola
    clausola WHERE, dove non produce alcuna colonna.
    """
    produced, wildcard = projected_columns(source)
    if wildcard:
        return []
    return [p for p in _TARGET_PLACEHOLDER_RE.findall(target)
            if p.lower() not in produced]


# ---------------------------------------------------------------------------
# isnan() su colonne testuali
#
# Il template usa ``NOT isnan(col)`` come filtro "valore numerico presente",
# pensato per colonne DOUBLE. Al caricamento di un CSV basta una sola cella non
# numerica (es. un codice di valore mancante come 'u') perche' DuckDB tipizzi
# l'intera colonna come VARCHAR, e su VARCHAR isnan() non esiste: il mapping
# generato fallisce in Ontop, e con lui ogni query che Ontop espande in una
# union che lo comprende.
#
# Su queste colonne il generatore sostituisce isnan() con un controllo
# sintattico "il testo e' un numero decimale", e nella proiezione converte la
# colonna in DOUBLE solo quando lo e'. TRY_CAST sarebbe piu' semplice ma il
# parser SQL di Ontop 5.3 non lo riconosce e l'endpoint non si avvia; questa
# forma e' stata verificata con Ontop 5.3.0 e DuckDB.
# ---------------------------------------------------------------------------

NUMERIC_TEXT_PATTERN = r"'[+-]?[0-9]+(\.[0-9]+)?'"

TEXT_TYPES = {"VARCHAR", "TEXT", "STRING", "CHAR", "BPCHAR"}

_ISNAN_RE = re.compile(
    r'(?P<not>\bNOT\s+)?\bisnan\s*\(\s*(?P<q>"?)(?P<col>[A-Za-z_]\w*)(?P=q)\s*\)',
    re.IGNORECASE,
)


def _is_text(sql_type):
    return (sql_type or "").upper().split("(")[0].strip() in TEXT_TYPES


def _numeric_text(col):
    return f"regexp_full_match(CAST({col} AS VARCHAR), {NUMERIC_TEXT_PATTERN})"


def adapt_isnan(source, column_types):
    """Riscrive isnan() sulle colonne che nel database del sito sono testo.

    ``column_types`` mappa nome di colonna (qualunque maiuscola) a tipo DuckDB
    per la tabella del source. Restituisce ``(source, colonne_riscritte)``.

    - ``NOT isnan(c)`` diventa ``regexp_full_match(CAST(c AS VARCHAR), ...)``;
    - ``isnan(c)`` diventa ``(NOT regexp_full_match(...))``;
    - nella proiezione di primo livello ``c`` diventa
      ``CASE WHEN <c numerico> THEN CAST(c AS DOUBLE) END AS c``, cosi' che il
      target riceva sempre un numero o NULL, mai il testo non numerico.

    Le colonne numeriche restano invariate: isnan() su DOUBLE e' corretto, e
    su interi DuckDB lo applica con un cast implicito.
    """
    types = {k.lower(): v for k, v in column_types.items()}
    rewritten = []

    def _replace(m):
        col = m.group("col")
        sql_type = types.get(col.lower())
        if not _is_text(sql_type):
            return m.group(0)
        if col not in rewritten:
            rewritten.append(col)
        check = _numeric_text(col)
        return check if m.group("not") else f"(NOT {check})"

    out = _ISNAN_RE.sub(_replace, source)
    if not rewritten:
        return source, []

    span = _projection_span(out)
    if span is not None:
        start, end = span
        items = _split_top_level(out[start:end])
        targets = {c.lower(): c for c in rewritten}
        new_items = []
        for item in items:
            plain = _PLAIN_COLUMN_RE.match(item)
            name = plain.group(1).strip('"') if plain else None
            if name is not None and name.lower() in targets:
                col = name
                item = (f'CASE WHEN {_numeric_text(col)} '
                        f"THEN CAST({col} AS DOUBLE) END AS {col}")
            new_items.append(item)
        out = out[:start] + " " + ", ".join(new_items) + " " + out[end:]

    return out, rewritten


# ---------------------------------------------------------------------------
# Salvataggio del mapping attivo
# ---------------------------------------------------------------------------

BACKUPS_KEPT = 10


def save_active_mapping(path, text, backup_dir, keep=BACKUPS_KEPT):
    """Scrive il mapping attivo in modo atomico, conservando la versione precedente.

    Prima il file era aperto in scrittura e troncato: un errore a meta' lo
    lasciava vuoto o parziale, e la versione precedente era persa. Ora il testo
    e' scritto in un file temporaneo nella stessa directory e sostituito con
    os.replace, e la versione precedente e' copiata in ``backup_dir`` con data
    e ora nel nome. Restano le ultime ``keep`` copie. Restituisce il percorso
    della copia, o None se non esisteva un mapping precedente.
    """
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path

    path, backup_dir = Path(path), Path(backup_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    backup = None
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = backup_dir / f"{path.name}.{stamp}"
        n = 1
        while backup.exists():
            backup = backup_dir / f"{path.name}.{stamp}-{n}"
            n += 1
        shutil.copy2(path, backup)

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    if backup_dir.exists():
        old = sorted((p for p in backup_dir.iterdir() if p.name.startswith(path.name + ".")),
                     key=lambda p: p.stat().st_mtime)
        for p in old[:-keep] if keep else old:
            p.unlink()
    return backup
