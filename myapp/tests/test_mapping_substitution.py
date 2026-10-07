"""Audit-test del punto 7: la sostituzione dei segnaposto deve essere
consapevole dei token, non per sottostringa.

Il generatore precedente applicava, per ogni coppia (segnaposto, colonna),
quattro sostituzioni di sottostringa in sequenza:

    src = src.replace(var + ',', col + ',')
    src = src.replace(var + ' ', col + ' ')
    src = src.replace(var + ')', col + ')')
    src = src.replace('(' + var, '(' + col)

Difetti dimostrati qui sotto:

1. Nessuna delle quattro ancore contiene il newline, e nei template i source
   vanno a capo. Un segnaposto che chiude una riga non veniva sostituito,
   mentre la sua occorrenza nella WHERE si: il mapping risultante selezionava
   la colonna vecchia e filtrava su quella nuova.
2. Le sostituzioni in sequenza propagano a cascata: rinominare a->b e poi b->c
   trasforma anche il primo token in c.
3. Nessuna protezione dei literal e dei nomi di tabella fra doppi apici.
"""

from django.test import SimpleTestCase

from myapp.obda_mapping import (
    parse_mappings,
    source_identifiers,
    substitute_identifiers,
    substitute_target_placeholders,
    unresolved_placeholders,
)

# Blocco preso alla lettera da myapp/mappings/template.obda
# (MAPID-f24debb89dc84f73a47acdf763ec0b4f): 'alive' compare due volte, la
# prima seguita da newline, la seconda da spazio.
TEMPLATE_BLOCK_SOURCE = (
    'SELECT patient, alive\n'
    '\t\t\tFROM "PATIENTS GENERAL DATA"\n'
    "\t\t\tWHERE alive IS NOT NULL"
)
TEMPLATE_BLOCK_TARGET = "bto:Patient{patient} bto:alive {alive}^^xsd:boolean ."


def legacy_substitute(src, target, renames):
    """La logica del generatore precedente, per confronto."""
    for var, col in renames.items():
        target = target.replace(f"{{{var}}}", "{" + col + "}")
        src = src.replace(var + ",", col + ",")
        src = src.replace(var + " ", col + " ")
        src = src.replace(var + ")", col + ")")
        src = src.replace("(" + var, "(" + col)
    return src, target


class SubstitutionTests(SimpleTestCase):

    def test_placeholder_at_end_of_line_is_substituted(self):
        """Il caso reale del template distribuito."""
        renames = {"patient": "Participant_ID", "alive": "VITAL_STATUS"}

        src = substitute_identifiers(TEMPLATE_BLOCK_SOURCE, renames)

        self.assertNotIn("alive", src,
                         f"occorrenza non sostituita:\n{src}")
        self.assertEqual(src.count("VITAL_STATUS"), 2,
                         f"attese 2 occorrenze riscritte:\n{src}")

    def test_legacy_behaviour_is_what_we_claim(self):
        """Controllo che il difetto diagnosticato fosse reale.

        Senza questo, il test precedente non dimostrerebbe una correzione.
        """
        renames = {"patient": "Participant_ID", "alive": "VITAL_STATUS"}

        legacy_src, legacy_tgt = legacy_substitute(
            TEMPLATE_BLOCK_SOURCE, TEMPLATE_BLOCK_TARGET, renames)

        # La SELECT conserva 'alive', la WHERE no: mapping incoerente.
        self.assertIn("SELECT Participant_ID, alive", legacy_src)
        self.assertIn("WHERE VITAL_STATUS IS NOT NULL", legacy_src)
        # E il target punta alla colonna nuova, che la SELECT non produce.
        self.assertIn("{VITAL_STATUS}", legacy_tgt)
        self.assertEqual(
            unresolved_placeholders(legacy_tgt, legacy_src), ["VITAL_STATUS"],
            "il generatore precedente produceva un target non soddisfacibile",
        )

    def test_substitution_does_not_cascade(self):
        """Rinominare a->b e b->c non deve trasformare a in c."""
        src = "SELECT a, b FROM \"t\""
        out = substitute_identifiers(src, {"a": "b", "b": "c"})
        self.assertEqual(out, 'SELECT b, c FROM "t"')

        legacy, _ = legacy_substitute(src, "", {"a": "b", "b": "c"})
        self.assertEqual(legacy, 'SELECT c, c FROM "t"',
                         "la versione precedente propagava a cascata")

    def test_quoted_table_name_is_never_rewritten(self):
        """Una colonna omonima della tabella non deve rinominare la tabella."""
        src = 'SELECT patient FROM "patient" WHERE patient IS NOT NULL'
        out = substitute_identifiers(src, {"patient": "Participant_ID"})
        self.assertEqual(
            out,
            'SELECT Participant_ID FROM "patient" WHERE Participant_ID IS NOT NULL',
        )

    def test_string_literals_are_never_rewritten(self):
        """Un literal che coincide col nome di una colonna resta intatto."""
        src = "SELECT patient, 'patient' AS kind FROM \"t\""
        out = substitute_identifiers(src, {"patient": "pid"})
        self.assertEqual(out, "SELECT pid, 'patient' AS kind FROM \"t\"")

    def test_alias_is_renamed_together_with_the_expression(self):
        """target e source restano d'accordo anche con un'espressione.

        Il template usa LCASE(sex) AS sex: rinominando sex -> SEX devono
        cambiare sia l'argomento sia l'alias, altrimenti il target proietta
        una colonna che il source non produce.
        """
        src = "SELECT patient, LCASE(sex) AS sex FROM \"t\" WHERE sex <> ''"
        tgt = "bto:Patient{patient} bto:sex {sex}^^xsd:string ."
        renames = {"patient": "Participant_ID", "sex": "SEX"}

        out_src = substitute_identifiers(src, renames)
        out_tgt = substitute_target_placeholders(tgt, renames)

        self.assertIn("LCASE(SEX) AS SEX", out_src)
        self.assertIn("{SEX}", out_tgt)
        self.assertEqual(unresolved_placeholders(out_tgt, out_src), [])

    def test_unmapped_placeholders_are_left_alone(self):
        """Un segnaposto senza associazione non va toccato."""
        tgt = "bto:x{a} bto:y{b} ."
        self.assertEqual(substitute_target_placeholders(tgt, {"a": "A"}),
                         "bto:x{A} bto:y{b} .")


class TemplateRoundTripTests(SimpleTestCase):
    """Ogni blocco del template distribuito deve sopravvivere a una rinomina."""

    def test_every_template_block_survives_a_full_rename(self):
        from django.conf import settings
        from pathlib import Path

        path = Path(settings.BASE_DIR) / "myapp" / "mappings" / "template.obda"
        blocks = parse_mappings(path.read_text(encoding="utf-8"))
        self.assertGreater(len(blocks), 20)

        broken_legacy, broken_new = [], []
        for block in blocks:
            # Rinomina sintetica ma esaustiva: ogni segnaposto prende un nome
            # nuovo e distinto, cosi' che una mancata sostituzione sia visibile.
            renames = {p: f"col_{i}" for i, p in enumerate(block.placeholders)}
            if not renames:
                continue

            new_src = substitute_identifiers(block.source, renames)
            new_tgt = substitute_target_placeholders(block.target, renames)
            if unresolved_placeholders(new_tgt, new_src):
                broken_new.append(block.mapping_id)

            old_src, old_tgt = legacy_substitute(block.source, block.target, renames)
            if unresolved_placeholders(old_tgt, old_src):
                broken_legacy.append(block.mapping_id)

        self.assertEqual(
            broken_new, [],
            f"blocchi ancora incoerenti dopo la rinomina: {broken_new}",
        )
        self.assertTrue(
            broken_legacy,
            "il confronto non e' significativo: la versione precedente "
            "avrebbe dovuto rompere almeno un blocco",
        )


class SourceIdentifierTests(SimpleTestCase):

    def test_quoted_identifiers_count_as_available_columns(self):
        names = source_identifiers('SELECT "odd name" FROM "t"')
        self.assertIn("odd name", names)
        self.assertIn("t", names)

    def test_literals_do_not_count(self):
        self.assertNotIn("c34373", source_identifiers(
            "SELECT patient, 'C34373' AS disease FROM \"t\""))


class ProjectedColumnsTests(SimpleTestCase):
    """La validazione deve guardare le colonne prodotte, non ogni identificatore."""

    def test_where_only_column_is_not_produced(self):
        """Il nucleo della correzione di unresolved_placeholders.

        'alive' compare nella WHERE ma non nella proiezione: non e' una colonna
        prodotta e non puo' soddisfare un segnaposto del target.
        """
        from myapp.obda_mapping import projected_columns

        names, wildcard = projected_columns(
            'SELECT patient FROM "t" WHERE alive IS NOT NULL')
        self.assertFalse(wildcard)
        self.assertEqual(names, {"patient"})

    def test_alias_defines_the_produced_name(self):
        from myapp.obda_mapping import projected_columns

        names, wildcard = projected_columns(
            "SELECT patient, LCASE(sex) AS sex, 'C34373' AS disease FROM \"t\"")
        self.assertFalse(wildcard)
        self.assertEqual(names, {"patient", "sex", "disease"})

    def test_from_inside_an_expression_does_not_end_the_projection(self):
        from myapp.obda_mapping import projected_columns

        names, _ = projected_columns(
            'SELECT EXTRACT(YEAR FROM d) AS yr, patient FROM "t"')
        self.assertEqual(names, {"yr", "patient"})

    def test_subquery_in_from_is_not_mistaken_for_the_projection(self):
        from myapp.obda_mapping import projected_columns

        names, wildcard = projected_columns(
            'SELECT patient, gene FROM (SELECT patient, \'FUS\' AS gene '
            'FROM "x" WHERE FUS IS NOT NULL) AS subquery')
        self.assertFalse(wildcard)
        self.assertEqual(names, {"patient", "gene"})

    def test_wildcard_suspends_the_check(self):
        """Con SELECT * non si puo' concludere offline: non si blocca il salvataggio."""
        from myapp.obda_mapping import projected_columns

        names, wildcard = projected_columns('SELECT * FROM "t"')
        self.assertTrue(wildcard)
        self.assertEqual(names, set())
        self.assertEqual(
            unresolved_placeholders("bto:x{anything} .", 'SELECT * FROM "t"'), [])

    def test_qualified_column_resolves_to_its_last_component(self):
        from myapp.obda_mapping import projected_columns

        names, _ = projected_columns('SELECT v1.Participant_ID FROM "t" AS v1')
        self.assertEqual(names, {"participant_id"})
