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
        blocks.append(MappingBlock(
            mapping_id=mid.group(1),
            target=tgt.group(1).strip(),
            source=" ".join(src.group(1).split()),
        ))
    return blocks
