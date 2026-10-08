"""Schema relazionale standard della modalita' Galois e suo mapping fisso.

Contiene solo conoscenza pubblica, che un LLM puo' avere: malattie, farmaci,
geni, siti anatomici, ospedali, trial clinici. Nessuna tabella e nessun
mapping riguarda bto:Patient o gli eventi del paziente: un LLM non conosce
dati di pazienti e li inventerebbe.

Le chiavi sono identificativi pubblici, cosi' che l'LLM non debba inventare id
e che gli IRI coincidano con quelli dell'ontologia:
- malattie, geni, siti anatomici: codice NCIT (es. C34373), IRI NCIT:C34373.
  Per le malattie si usa NCIT, come il catalogo delle query e i dati clinici
  (bto:hasDisease NCIT:...), anche se gli individui DiseaseDisorderOrFinding
  dell'ontologia sono SNOMED;
- farmaci: codice ATC, IRI http://purl.bioontology.org/ontology/UATC/<codice>,
  come gli individui bto:PharmacologicSubstance dell'ontologia;
- ospedali: sito web, che e' l'IRI degli individui bto:ClinicsAndHospitals;
- trial clinici: identificativo NCT, IRI https://clinicaltrials.gov/study/<id>.

L'amministratore puo' attivare o disattivare tabelle e colonne; la chiave non
si puo' togliere. Ogni colonna ha il proprio blocco di mapping, che non si
modifica.
"""
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

PREFIXES = {
    "bto": "https://w3id.org/brainteaser/ontology/schema/",
    "NCIT": "http://purl.obolibrary.org/obo/NCIT_",
    "UATC": "http://purl.bioontology.org/ontology/UATC/",
    "CT": "https://clinicaltrials.gov/study/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
 "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

_NCIT_RE = re.compile(r"C[1-9][0-9]{0,9}")
_ATC_RE = re.compile(r"[A-Z](?:[0-9]{2}(?:[A-Z](?:[A-Z](?:[0-9]{2})?)?)?)?")
_NCT_RE = re.compile(r"NCT[0-9]{8}")
_URL_RE = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%!$&'()*+,;=:@/-]*)?")
MAX_TEXT = 2000


def _text(v):
    v = " ".join(str(v).split())
    return v[:MAX_TEXT] or None


def _ncit(v):
    v = str(v).strip().upper()
    for p in ("NCIT:", "NCIT_", "NCI:"):
        if v.startswith(p):
            v = v[len(p):]
    return v if _NCIT_RE.fullmatch(v) else None


def _atc(v):
    v = str(v).strip().upper().replace(" ", "")
    return v if _ATC_RE.fullmatch(v) else None


def _nct(v):
    v = str(v).strip().upper()
    return v if _NCT_RE.fullmatch(v) else None


def _url(v):
    v = str(v).strip()
    return v if _URL_RE.fullmatch(v) else None


@dataclass(frozen=True)
class Column:
    name: str
    description: str
    predicate: str          # termine dell'ontologia
    object: str             # oggetto del triple pattern, con {colonna}
    clean: Callable = _text
    references: Optional[str] = None   # tabella referenziata (chiave esterna)
    default: bool = True


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    key: str
    key_clean: Callable
    subject: str            # IRI template del soggetto, con {chiave}
    cls: str                # classe dell'ontologia
    columns: Tuple[Column, ...] = field(default_factory=tuple)

    def column(self, name):
        return next(c for c in self.columns if c.name == name)


def _label(col="name", desc="name", default=True):
    return Column(col, desc, "rdfs:label", "{%s}^^xsd:string" % col, default=default)


def _comment(col="description", desc="short definition", default=False):
    return Column(col, desc, "rdfs:comment", "{%s}^^xsd:string" % col, default=default)


TABLES = (
    Table("disease", "Diseases, disorders and findings", "ncit_code", _ncit,
          "NCIT:{ncit_code}", "bto:DiseaseDisorderOrFinding",
          (_label(), _comment())),
    Table("drug", "Pharmacologic substances and ATC groups", "atc_code", _atc,
          "UATC:{atc_code}", "bto:PharmacologicSubstance",
          (_label(),
           Column("parent_atc_code", "ATC code of the parent group", "skos:broaderTransitive",
                  "UATC:{parent_atc_code}", _atc, references="drug"))),
    Table("gene", "Genes", "ncit_code", _ncit, "NCIT:{ncit_code}", "bto:Gene",
          (_label(), _comment())),
    Table("anatomical_site", "Anatomical sites", "ncit_code", _ncit,
          "NCIT:{ncit_code}", "bto:AnatomicalSite", (_label(),)),
    Table("hospital", "Clinics and hospitals", "website", _url, "<{website}>",
          "bto:ClinicsAndHospitals", (_label(),)),
    Table("clinical_trial", "Clinical trials", "nct_id", _nct, "CT:{nct_id}",
          "bto:ClinicalTrial",
          (Column("title", "title", "rdfs:label", "{title}^^xsd:string"),
           Column("description", "brief summary", "bto:clinicalTrialDescription",
                  "{description}^^rdf:PlainLiteral"), # range dichiarato nell ontologia
           Column("disease_ncit_code", "NCIT code of the studied disease", "bto:isAboutDisease",
                  "NCIT:{disease_ncit_code}", _ncit, references="disease"),
           Column("hospital_website", "website of the conducting hospital",
                  "bto:conductedInClinic", "<{hospital_website}>", _url,
                  references="hospital", default=False))),
)

BY_NAME = {t.name: t for t in TABLES}


def default_config():
    return {t.name: {"enabled": True, "columns": [c.name for c in t.columns if c.default]}
            for t in TABLES}


def active_columns(table, table_cfg):
    """Colonne attive, nell'ordine dello schema; la chiave e' sempre la prima."""
    wanted = set(table_cfg.get("columns", []))
    return [table.key] + [c.name for c in table.columns if c.name in wanted]


def mapping_blocks(table, columns):
    """``[(mappingId, target, source)]`` del mapping fisso per le colonne date."""
    q = f'"{table.name}"'
    blocks = [(f"GALOIS-{table.name}", f"{table.subject} a {table.cls} .",
               f"SELECT {table.key} FROM {q}")]
    for name in columns[1:]:
        c = table.column(name)
        blocks.append((f"GALOIS-{table.name}-{c.name}",
                       f"{table.subject} {c.predicate} {c.object} .",
                       f"SELECT {table.key}, {c.name} FROM {q} WHERE {c.name} IS NOT NULL"))
    return blocks


def obda_text(blocks):
    lines = ["[PrefixDeclaration]"]
    lines += [f"{p}:\t\t{iri}" for p, iri in PREFIXES.items()]
    lines += ["", "[MappingDeclaration] @collection [["]
    for mid, target, source in blocks:
        lines += [f"mappingId\t{mid}", f"target\t\t{target}", f"source\t\t{source}", ""]
    lines.append("]]")
    return "\n".join(lines) + "\n"
