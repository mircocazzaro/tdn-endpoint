"""Audit-test del punto 8: il catalogo delle query ammesse.

Prima di questa correzione il catalogo esisteva in tre forme non allineate:

- ``myapp/management/queries_catalog.md``, la fonte dichiarata, caricata da
  ``load_allowed_queries``: 14 template di una generazione precedente, di cui
  13 sconosciuti al catalogo in uso, 6 con predicati che HERO non dichiara
  (bto:hasEvent, bto:registeredFor, bto:hasName, bto:procedureType), 2 con
  ``NCIT_C0002736``, che e' un CUI UMLS e non il codice NCIT della SLA, e 12
  con i marcatori markdown ``**<disease>**`` dentro il testo, quindi dentro
  l'hash;
- ``uploads/allowed_queries.duckdb``, il catalogo effettivamente usato:
  15 template; in 14 righe su 15 la colonna ``query`` conteneva un testo con
  fine riga CRLF che non aveva l'hash della propria colonna ``hash``;
- ``uploads/allquery.csv``, esportazione della generazione markdown.

Il catalogo e' ora codice, in ``myapp/catalog.py``, ricavato da quello di HDN
Central. Questi test verificano le invarianti che il formato precedente non
poteva garantire.
"""

import dataclasses
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from myapp import catalog

HERO_TTL = Path(settings.BASE_DIR) / "myapp" / "obda" / "hero_clinical.ttl"

# Prefisso dell'hash con cui il catalogo precedente riconosceva ogni template,
# letto dalla colonna ``hash`` di uploads/allowed_queries.duckdb prima della
# sua rimozione. E' l'hash del campo ``template`` che Central invia oggi:
# l'endpoint deve continuare a riconoscerli tutti.
PREVIOUS_LIVE_HASHES = {
    "q00_L0": "2b38956263bba610",
    "q01_L0": "ef1c7e06dc45f63a",
    "q02_L1": "3ade72ee584fd40c",
    "q03_L1": "bf4a6509fabddd04",
    "q04_L2": "8debe5df598a877a",
    "q06_L3": "1ff81106904443fb",
    "q07_L3": "8fc48dea866d4558",
    "q08_L3": "4526a48277c9bba7",
    "q09_L4": "5b8a44bf0364eb71",
    "q10_L4": "58d92e8b94bbbb84",
    "q11_L5": "bcdb63546def4177",
    "q12_L5": "c208b60883a18b76",
    "q13_L6": "1f996ff13f55a035",
    "q14_L6": "eea9ba3ad603cfce",
}

# Valori che il form di Central produce oggi per ciascun tipo di parametro.
CENTRAL_VALUES = {
    "disease": ["NCIT:C34373", "NCIT:C3243", "NCIT:C26845"],
    "age": ["40", "65", "0", "99.5"],
    "sex": ["female", "male", "Female"],
    "alsfrs_question": [
        f"<https://w3id.org/brainteaser/ontology/schema/alsfrs{i}>" for i in range(1, 13)
    ],
}

# Valori che non devono passare: ognuno prova a uscire dalla posizione del
# parametro o ad aggiungere SPARQL.
HOSTILE_VALUES = [
    "",
    " NCIT:C34373",
    "NCIT:C34373 .",
    "NCIT:C34373 . ?pat ?p ?o",
    "NCIT:C34373 } UNION { ?s ?p ?o",
    "NCIT:C34373#",
    "NCIT:C34373\n",
    "?x",
    "<urn:x>",
    "female\" ; bto:x \"y",
    "40) || (1 = 1",
    "40 #",
    "1e9",
    "-1",
    "<https://w3id.org/brainteaser/ontology/schema/alsfrs13>",
    "<https://w3id.org/brainteaser/ontology/schema/alsfrs1> || true",
]


def _hero_terms():
    text = HERO_TTL.read_text(encoding="utf-8")
    return set(re.findall(
        r"^###\s+https://w3id\.org/brainteaser/ontology/schema/([A-Za-z0-9_]+)\s*$",
        text, flags=re.M))


class CatalogIntegrityTests(SimpleTestCase):

    def test_catalog_is_complete(self):
        self.assertEqual(
            [t.key for t in catalog.CATALOG], list(PREVIOUS_LIVE_HASHES),
            "il catalogo non contiene esattamente i template attesi")

    def test_every_template_is_recognized_as_before(self):
        """Ogni template mantiene l'hash con cui l'endpoint lo riconosceva.

        Se questo cambia, l'endpoint smette di riconoscere le richieste di
        Central e, per il punto 5, smette di contribuire in silenzio.
        """
        for t in catalog.CATALOG:
            with self.subTest(template=t.key):
                self.assertTrue(
                    t.wire_sha512.startswith(PREVIOUS_LIVE_HASHES[t.key]),
                    f"{t.key}: hash sul filo {t.wire_sha512[:16]}, "
                    f"atteso {PREVIOUS_LIVE_HASHES[t.key]}")
                fields = {"template": catalog.central_mask(t.sparql)}
                self.assertIs(catalog.lookup_by_template_text(fields["template"]), t)

    def test_a_modified_template_text_is_rejected(self):
        """La proprieta' che rende il formato solido: nessuna deriva silenziosa.

        Rimuovere uno spazio finale, come fa un editor al salvataggio, cambia
        l'hash del template. Il catalogo deve rifiutare di caricarsi, non
        cominciare a rifiutare in silenzio le richieste di Central.
        """
        q06 = catalog.BY_KEY["q06_L3"]
        self.assertIn(" \n", q06.sparql, "il caso di prova non ha uno spazio finale")
        stripped = dataclasses.replace(q06, sparql=q06.sparql.replace(" \n", "\n"))

        with self.assertRaises(catalog.CatalogIntegrityError) as ctx:
            catalog.verify([stripped])
        self.assertIn("hash", str(ctx.exception))

    def test_crlf_line_endings_are_rejected(self):
        """Il difetto del catalogo precedente: testo CRLF con hash calcolato su LF."""
        q00 = catalog.BY_KEY["q00_L0"]
        crlf = dataclasses.replace(q00, sparql=q00.sparql.replace("\n", "\r\n"))
        with self.assertRaises(catalog.CatalogIntegrityError):
            catalog.verify([crlf])

    def test_undeclared_placeholder_is_rejected(self):
        q00 = catalog.BY_KEY["q00_L0"]
        broken = dataclasses.replace(q00, params={})
        with self.assertRaises(catalog.CatalogIntegrityError) as ctx:
            catalog.verify([broken])
        self.assertIn("senza grammatica", str(ctx.exception))

    def test_excluded_template_is_not_recognized(self):
        """q05_L2 resta fuori: ne' l'endpoint precedente ne' questo lo riconoscono."""
        central_q05 = (
            'SELECT (AVG(xsd:integer(?diff)) AS ?medianSurvivalDays) WHERE {\n'
            '  ?pat a bto:Patient ;\n'
            '       bto:hasDisease {disease} ;\n'
            '       bto:deathDate ?d .\n'
            '  ?ev  a bto:Onset ;\n'
            '       bto:eventStart ?s ;\n'
            '       bto:registeredFor ?pat .\n'
            '  FILTER ( bto:eventStart >= "{starting_date}"^^xsd:date )\n'
            '  BIND( xsd:integer(?d) - xsd:integer(?s) AS ?diff )\n'
            '}'
        )
        self.assertIsNone(catalog.lookup_by_template_text(central_q05))


class CatalogContentTests(SimpleTestCase):

    def test_no_markdown_markers_in_any_template(self):
        for t in catalog.CATALOG:
            with self.subTest(template=t.key):
                self.assertNotIn("**", t.sparql)

    def test_every_ontology_term_exists_in_hero(self):
        """Ogni termine bto: usato dai template e' dichiarato in HERO.

        E' il controllo che il catalogo markdown non superava: 6 dei suoi
        template usavano predicati che HERO non dichiara.
        """
        declared = _hero_terms()
        self.assertGreater(len(declared), 100, "parsing di HERO fallito")

        for t in catalog.CATALOG:
            used = set(re.findall(r"\bbto:([A-Za-z0-9_]+)", t.sparql))
            used |= set(re.findall(
                r"<https://w3id\.org/brainteaser/ontology/schema/([A-Za-z0-9_]+)>",
                t.sparql))
            with self.subTest(template=t.key):
                self.assertEqual(sorted(used - declared), [],
                                 f"{t.key}: termini assenti da HERO")

    def test_als_is_identified_by_its_ncit_code(self):
        """I template che fissano la malattia usano NCIT:C34373, non il CUI UMLS."""
        for t in catalog.CATALOG:
            with self.subTest(template=t.key):
                self.assertNotIn("C0002736", t.sparql)

    def test_every_level_has_at_least_one_template(self):
        self.assertEqual(sorted({t.level for t in catalog.CATALOG}), list(catalog.LEVELS))


class ParameterGrammarTests(SimpleTestCase):

    def _types(self):
        types = {}
        for t in catalog.CATALOG:
            for ptype in t.params.values():
                types[ptype.name] = ptype
        return types

    def test_every_parameter_type_is_covered(self):
        self.assertEqual(set(self._types()), set(CENTRAL_VALUES))

    def test_grammars_accept_what_central_produces(self):
        for name, ptype in self._types().items():
            for value in CENTRAL_VALUES[name]:
                with self.subTest(param=name, value=value):
                    self.assertTrue(ptype.accepts(value))

    def test_grammars_reject_values_that_could_add_sparql(self):
        for name, ptype in self._types().items():
            for value in HOSTILE_VALUES:
                with self.subTest(param=name, value=value):
                    self.assertFalse(ptype.accepts(value))
