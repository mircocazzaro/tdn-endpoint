"""Audit-test del punto 7, lato view: il file generato deve essere coerente,
e un mapping incoerente non deve essere scritto.

Il generatore scriveva ``hereditary_ontology_2.obda`` in place, troncandolo,
senza alcuna validazione. Dato il difetto di sostituzione, ogni salvataggio
poteva degradare il file che definisce il contratto semantico con HERO, e
l'utente vedeva comunque il messaggio di conferma.
"""

import json
import tempfile
from pathlib import Path

import duckdb
from django.test import Client, TestCase

from myapp import views
from myapp.obda_mapping import parse_mappings, unresolved_placeholders

# 'alive' chiude la riga della SELECT ed e' seguito da spazio nella WHERE:
# e' la forma che il generatore precedente riscriveva solo a meta'.
TEMPLATE = '''[PrefixDeclaration]
:		https://w3id.org/hereditary/ontology/schema/
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-ALIVE
target		bto:Patient{patient} bto:alive {alive}^^xsd:boolean . 
source		SELECT patient, alive
			FROM "SRC"
			WHERE alive IS NOT NULL
]]
'''

# Il target proietta {ghost}, che la sorgente non produce in nessun caso.
TEMPLATE_INCONSISTENT = '''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/

[MappingDeclaration] @collection [[
mappingId	MAPID-GHOST
target		bto:Patient{patient} bto:x {ghost}^^xsd:string . 
source		SELECT patient FROM "SRC"
]]
'''


class MappingGenerationViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)

        self.db_path = str(tmp / "src.duckdb")
        con = duckdb.connect(self.db_path)
        con.execute('CREATE TABLE "SRC" (pid VARCHAR, status BOOLEAN)')
        con.execute("INSERT INTO \"SRC\" VALUES ('p1', true)")
        con.close()

        self.template_path = tmp / "template.obda"
        self.out_path = tmp / "generated.obda"

        # La view usa costanti a livello di modulo, non impostazioni.
        for name, value in [
            ("DUCKDB_PATH", self.db_path),
            ("TEMPLATE_OBDA", str(self.template_path)),
            ("OBDA_FILE", str(self.out_path)),
            ("ONTOP_DIR", str(tmp)),
        ]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)

    def _post(self, connections, mapping_id):
        return self.client.post("/map-fields/", {
            f"{mapping_id}__table": "SRC",
            f"connections_{mapping_id}": json.dumps(connections),
        })

    def test_generated_file_is_internally_consistent(self):
        """Ogni segnaposto del target deve essere prodotto dal suo source."""
        self.template_path.write_text(TEMPLATE, encoding="utf-8")

        resp = self._post({"patient": "pid", "alive": "status"}, "MAPID-ALIVE")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.out_path.exists(), "file non scritto")

        blocks = parse_mappings(self.out_path.read_text(encoding="utf-8"))
        self.assertEqual(len(blocks), 1)
        block = blocks[0]

        # Entrambe le occorrenze riscritte, SELECT compresa.
        self.assertNotIn("alive", block.source,
                         f"occorrenza non sostituita: {block.source!r}")
        self.assertIn("status", block.source)
        self.assertIn("{status}", block.target)
        self.assertEqual(unresolved_placeholders(block.target, block.source), [])

    def test_inconsistent_mapping_is_not_written(self):
        """Un target non soddisfacibile non deve finire nel file attivo."""
        self.template_path.write_text(TEMPLATE_INCONSISTENT, encoding="utf-8")
        self.out_path.write_text("CONTENUTO PRECEDENTE", encoding="utf-8")

        resp = self._post({"patient": "pid"}, "MAPID-GHOST")

        self.assertEqual(resp.status_code, 200,
                         "la view ha rediretto: il salvataggio e' avvenuto")
        self.assertEqual(self.out_path.read_text(encoding="utf-8"),
                         "CONTENUTO PRECEDENTE",
                         "il file attivo e' stato sovrascritto con un mapping rotto")

        messages = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("ghost" in m for m in messages),
                        f"nessun errore riportato all'utente: {messages}")
        self.assertTrue(any("lasciato invariato" in m for m in messages),
                        f"l'utente non e' informato del mancato salvataggio: {messages}")

    def test_empty_connections_field_does_not_break_the_request(self):
        """Il campo nascosto vuoto non deve far fallire la richiesta.

        La UI azzera connections_<id> mentre carica le colonne; un submit in
        quella finestra arrivava a json.loads('') e abortiva la richiesta.
        """
        self.template_path.write_text(TEMPLATE, encoding="utf-8")

        resp = self.client.post("/map-fields/", {
            "MAPID-ALIVE__table": "SRC",
            "connections_MAPID-ALIVE": "",
        })

        self.assertIn(resp.status_code, (200, 302))

    def test_malformed_active_file_does_not_lock_the_page(self):
        """Con un .obda attivo illeggibile la pagina deve restare raggiungibile.

        Il percorso di lettura faceva 'raw.split("[MappingDeclaration]", 1)' e
        poi '.group(1)' senza guardie: un file troncato produceva ValueError e
        la pagina andava in 500, rendendo inaccessibile l'unico strumento per
        ripararlo.
        """
        self.template_path.write_text(TEMPLATE, encoding="utf-8")
        self.out_path.write_text("file troncato, nessuna sezione", encoding="utf-8")

        resp = self.client.get("/map-fields/")

        self.assertEqual(resp.status_code, 200)
        messages = [str(m) for m in resp.context["messages"]]
        self.assertTrue(any("non interpretabile" in m for m in messages),
                        f"nessun avviso sul file illeggibile: {messages}")
