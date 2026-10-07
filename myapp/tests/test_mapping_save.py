"""Audit-test del punto 18 (parte restante): salvataggio del mapping attivo.

Prima il generatore apriva hereditary_ontology_2.obda in scrittura e lo
troncava: un errore a meta' lo lasciava vuoto o parziale, la versione
precedente era persa, e Ontop, che legge il mapping solo all'avvio, continuava
a rispondere con quello vecchio mentre la pagina diceva "Mappings definition
stored!".

Ontop e' simulato: arresto e riavvio reali sono coperti dal punto 11.
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

import duckdb
from django.contrib.messages import get_messages
from django.test import Client, SimpleTestCase, TestCase

from myapp import ontop_process, views
from myapp.obda_mapping import save_active_mapping

TEMPLATE = '''[PrefixDeclaration]
bto:		https://w3id.org/brainteaser/ontology/schema/
xsd:		http://www.w3.org/2001/XMLSchema#

[MappingDeclaration] @collection [[
mappingId	MAPID-SEX
target		bto:Patient{patient} bto:sex {sex}^^xsd:string . 
source		SELECT patient, sex FROM "SRC"
]]
'''


class SaveActiveMappingTests(SimpleTestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "active.obda"
        self.backups = self.dir / "mapping-backups"

    def test_previous_version_is_kept(self):
        self.assertIsNone(save_active_mapping(self.path, "v1", self.backups))
        backup = save_active_mapping(self.path, "v2", self.backups)
        self.assertEqual(self.path.read_text(), "v2")
        self.assertEqual(backup.read_text(), "v1")

    def test_failed_write_leaves_the_active_file_intact(self):
        save_active_mapping(self.path, "versione buona", self.backups)
        with mock.patch("os.replace", side_effect=OSError("disco pieno")):
            with self.assertRaises(OSError):
                save_active_mapping(self.path, "versione nuova", self.backups)
        self.assertEqual(self.path.read_text(), "versione buona")
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")], [],
                         "file temporaneo rimasto")

    def test_only_the_last_backups_are_kept(self):
        for i in range(15):
            save_active_mapping(self.path, f"v{i}", self.backups, keep=10)
        kept = sorted(p.read_text() for p in self.backups.iterdir())
        self.assertEqual(len(kept), 10)
        self.assertIn("v13", kept)
        self.assertNotIn("v0", kept)


class MappingSaveViewTests(TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        db = tmp / "site.duckdb"
        con = duckdb.connect(str(db))
        con.execute('CREATE TABLE "SRC" (pid VARCHAR, s VARCHAR)')
        con.close()
        (tmp / "template.obda").write_text(TEMPLATE)
        self.active = tmp / "obda" / "active.obda"
        for name, value in [("DUCKDB_PATH", str(db)), ("TEMPLATE_OBDA", str(tmp / "template.obda")),
                            ("OBDA_FILE", str(self.active)), ("ONTOP_DIR", str(tmp / "obda"))]:
            original = getattr(views, name)
            setattr(views, name, value)
            self.addCleanup(setattr, views, name, original)

    def _save(self, sex_column="s"):
        resp = Client().post("/map-fields/", {
            "MAPID-SEX__table": "SRC",
            "connections_MAPID-SEX": json.dumps({"patient": "pid", "sex": sex_column}),
        })
        return resp, [(m.level_tag, str(m)) for m in get_messages(resp.wsgi_request)]

    def _ontop(self, running):
        calls = []
        patches = {
            "is_running": lambda: running,
            "stop": lambda timeout=None: calls.append("stop") or True,
            "start": lambda: calls.append("start"),
            "wait_ready": lambda timeout=None: calls.append("wait_ready") or True,
        }
        for name, fn in patches.items():
            p = mock.patch.object(ontop_process, name, fn)
            p.start()
            self.addCleanup(p.stop)
        return calls

    def test_running_ontop_is_restarted_to_load_the_new_mapping(self):
        calls = self._ontop(running=True)
        resp, msgs = self._save()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(calls, ["stop", "start", "wait_ready"])
        self.assertTrue(any(lvl == "success" and "new mapping" in t for lvl, t in msgs), msgs)

    def test_stopped_ontop_is_not_started_and_the_admin_is_told(self):
        calls = self._ontop(running=False)
        _, msgs = self._save()
        self.assertEqual(calls, [])
        self.assertTrue(any(lvl == "info" and "not running" in t for lvl, t in msgs), msgs)

    def test_second_save_keeps_the_first_as_backup(self):
        self._ontop(running=False)
        self._save()
        first = self.active.read_text()
        self._save()
        backups = list((self.active.parent / "mapping-backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), first)
