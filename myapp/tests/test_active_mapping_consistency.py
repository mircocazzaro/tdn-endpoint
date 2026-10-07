"""Audit-test del punto 6: il mapping attivo non deve asserire cio' che la
sorgente non contiene, e ogni suo source SQL deve essere eseguibile.

Stato prima della correzione, in ``myapp/obda/hereditary_ontology_2.obda``:

1. Le otto mappature di comorbidita' (ipertensione, diabete, dislipidemia,
   tiroide, autoimmunita', stroke, cardiopatia, neoplasia) avevano tutte
   ``WHERE GUID = True``, mentre il template da cui derivano usava una colonna
   diversa per ciascuna. Due problemi insieme:

     - ``aals_dataportal_datatable`` non ha alcuna colonna di comorbidita', per
       cui quelle otto asserzioni non hanno sorgente;
     - ``GUID`` e' VARCHAR e contiene stringhe tipo 'un identificativo alfanumerico', quindi il
       predicato solleva ConversionException a tempo di esecuzione. Poiche'
       Ontop espande una query SPARQL in una union sui mapping pertinenti,
       l'errore si propaga a query che non chiedono comorbidita'.

2. ``AGE_AT_SYMPTOM_ONSET`` (BIGINT, un'eta' in anni) era mappata su
   ``bto:eventStart`` con datatype ``xsd:datetime``, oltre che correttamente su
   ``bto:ageOnset``. La sorgente non ha alcuna colonna di data di onset.

Il test verifica le proprieta' corrispondenti sul file attivo, e inoltre fissa
l'insieme dei mapping ancora non eseguibili, cosi' che una nuova rottura venga
rilevata subito.
"""

import unittest
from pathlib import Path

import duckdb
from django.conf import settings

from myapp.obda_mapping import parse_mappings

OBDA_FILE = Path(settings.BASE_DIR) / "myapp" / "obda" / "hereditary_ontology_2.obda"
DUCKDB_FILE = Path(settings.BASE_DIR) / "myapp" / "obda" / "mydatabase.duckdb"

# Mapping il cui source SQL non e' ancora eseguibile sulla sorgente locale.
# Sono i due che filtrano con isnan() su colonne VARCHAR (Vital_Signs.weight e
# Vital_Signs.height): DuckDB non ha isnan(VARCHAR) e solleva BinderException.
# E' il problema registrato nel punto 12 dell'assessment, fuori dallo scope di
# questa correzione: qui viene solo fissato per impedire che l'insieme cresca.
KNOWN_NON_EXECUTABLE = {
    "MAPID-85b6967eb0034ec1a2f6131b80385266",
    "MAPID-325ab5a214d6481a8f99ee90d5bb72f3",
}


def _load_blocks():
    return parse_mappings(OBDA_FILE.read_text(encoding="utf-8"))


class ActiveMappingConsistencyTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.blocks = _load_blocks()
        if not cls.blocks:
            raise AssertionError("nessun mapping estratto dal file attivo")

    def test_the_parser_sees_the_whole_file(self):
        """Guardia sul parser: se smettesse di estrarre blocchi, tutti i
        controlli successivi passerebbero a vuoto."""
        self.assertGreaterEqual(len(self.blocks), 10)
        self.assertTrue(all(b.source.upper().startswith("SELECT")
                            for b in self.blocks))

    # --------------------------------------------------- asserzioni senza dati

    def test_no_comorbidity_without_a_source_column(self):
        """Nessun mapping dichiara bto:Comorbidity: la sorgente non le ha."""
        offending = [b.mapping_id for b in self.blocks
                     if "bto:Comorbidity" in b.target]
        self.assertEqual(
            offending, [],
            "mapping che asseriscono comorbidita' assenti dalla sorgente: "
            f"{offending}",
        )

    def test_no_mapping_filters_on_the_guid_column_as_boolean(self):
        """Nessun source confronta GUID (VARCHAR) con un literal booleano."""
        offending = [b.mapping_id for b in self.blocks
                     if "GUID = True" in b.source or "GUID = true" in b.source]
        self.assertEqual(offending, [],
                         f"confronto GUID = True ancora presente in: {offending}")

    def test_age_at_onset_is_never_typed_as_a_date(self):
        """Un'eta' in anni non va tipizzata come xsd:datetime o xsd:date."""
        offending = []
        for b in self.blocks:
            for placeholder, datatype in b.typed_placeholders:
                if placeholder.upper() == "AGE_AT_SYMPTOM_ONSET" and \
                        datatype.lower() in ("xsd:datetime", "xsd:date"):
                    offending.append((b.mapping_id, placeholder, datatype))
        self.assertEqual(offending, [],
                         f"eta' di onset tipizzata come data in: {offending}")

    def test_age_at_onset_is_still_exposed_as_ageonset(self):
        """La correzione non deve far perdere il dato: bto:ageOnset resta.

        Controllo negativo del test precedente: rimuovere il mapping invece di
        correggerne il datatype lo farebbe passare ugualmente.
        """
        exposed = [b.mapping_id for b in self.blocks
                   if "bto:ageOnset" in b.target
                   and "AGE_AT_SYMPTOM_ONSET" in b.target]
        self.assertTrue(exposed,
                        "nessun mapping espone piu' bto:ageOnset")

    # ------------------------------------------------- eseguibilita' dei source

    @unittest.skipUnless(DUCKDB_FILE.exists(), "database locale non presente")
    def test_every_target_placeholder_is_produced_by_its_source(self):
        """Ogni {segnaposto} del target deve esistere fra le colonne che il
        source produce davvero.

        E' il controllo che manca nel generatore di mapping e che consente ai
        due artefatti di divergere in silenzio.
        """
        con = duckdb.connect(str(DUCKDB_FILE), read_only=True)
        self.addCleanup(con.close)

        problems = []
        for b in self.blocks:
            if b.mapping_id in KNOWN_NON_EXECUTABLE:
                continue
            try:
                produced = {d[0].lower() for d in
                            con.execute(f"SELECT * FROM ({b.source}) LIMIT 0").description}
            except Exception as exc:
                problems.append(f"{b.mapping_id}: source non analizzabile: "
                                f"{type(exc).__name__}")
                continue
            missing = [p for p in b.placeholders if p.lower() not in produced]
            if missing:
                problems.append(f"{b.mapping_id}: segnaposto senza colonna "
                                f"corrispondente {missing} (prodotte: {sorted(produced)})")

        self.assertEqual(problems, [], "\n".join(problems))

    @unittest.skipUnless(DUCKDB_FILE.exists(), "database locale non presente")
    def test_the_set_of_non_executable_mappings_does_not_grow(self):
        """Esegue ogni source contro il database locale.

        Ontop inoltra il source verbatim nell'SQL che genera: un source che non
        esegue non puo' contribuire a nessuna risposta e, nelle union, rompe
        anche query che non lo riguardano.
        """
        con = duckdb.connect(str(DUCKDB_FILE), read_only=True)
        self.addCleanup(con.close)

        failing = {}
        for b in self.blocks:
            try:
                con.execute(f"SELECT count(*) FROM ({b.source})").fetchone()
            except Exception as exc:
                failing[b.mapping_id] = f"{type(exc).__name__}: " \
                                        f"{str(exc).splitlines()[0][:90]}"

        self.assertEqual(
            set(failing), KNOWN_NON_EXECUTABLE,
            "l'insieme dei mapping non eseguibili e' cambiato.\n"
            f"attesi: {sorted(KNOWN_NON_EXECUTABLE)}\n"
            f"trovati: {dict(sorted(failing.items()))}",
        )
