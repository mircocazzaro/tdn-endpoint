"""Knowledge graph RDF: cache, invalidazione e pulsante. Ontop e' simulato (subprocess)."""
import subprocess
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from myapp import knowledge_graph as kg
from myapp import ontop_process

from .hdn_helpers import StateDirMixin

MAPPING_TEXT = "[MappingDeclaration] @collection [[\nmappingId\tPAT\ntarget\t:p/{id} :a {x} .\nsource\tSELECT id, x FROM t\n]]\n"


def _fake_materialize(calls, body="<s> <p> <o> .\n", returncode=0, stderr=""):
    def run(cmd, **kwargs):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1])
        if returncode == 0:
            out.write_text(body, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
    return run


class KnowledgeGraphTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.obda = Path(self._state.name) / "mapping.obda"
        self.obda.write_text(MAPPING_TEXT, encoding="utf-8")
        p = mock.patch.object(ontop_process, "OBDA_FILE", self.obda)
        p.start()
        self.addCleanup(p.stop)

    def test_no_mapping_means_unavailable(self):
        self.obda.write_text("[PrefixDeclaration]\n:\thttp://x/\n", encoding="utf-8")
        self.assertFalse(kg.has_mappings())
        self.assertEqual(kg.status(), {"available": False, "cached": False})
        with self.assertRaises(kg.KnowledgeGraphError):
            kg.get_or_materialize()

    def test_materialize_uses_the_active_mapping_and_turtle(self):
        calls = []
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=_fake_materialize(calls)):
            path, cached, _ = kg.get_or_materialize()
        self.assertFalse(cached)
        cmd = calls[0]
        self.assertEqual(cmd[1], "materialize")
        self.assertEqual(cmd[cmd.index("-m") + 1], str(self.obda))
        self.assertEqual(cmd[cmd.index("-f") + 1], "turtle")
        self.assertIn("-t", cmd)
        self.assertIn("-p", cmd)
        self.assertEqual(path.read_text(encoding="utf-8"), "<s> <p> <o> .\n")

    def test_cache_is_reused_while_mapping_and_data_do_not_change(self):
        calls = []
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=_fake_materialize(calls)):
            kg.get_or_materialize()
            _, cached, _ = kg.get_or_materialize()
        self.assertTrue(cached)
        self.assertEqual(len(calls), 1)
        self.assertTrue(kg.status()["cached"])

    def test_changed_mapping_invalidates_the_cache(self):
        calls = []
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=_fake_materialize(calls)):
            kg.get_or_materialize()
            self.obda.write_text(MAPPING_TEXT + "# edited\n", encoding="utf-8")
            self.assertFalse(kg.status()["cached"])
            _, cached, _ = kg.get_or_materialize()
        self.assertFalse(cached)
        self.assertEqual(len(calls), 2)

    def test_data_changes_invalidate_the_cache(self):
        calls = []
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=_fake_materialize(calls)):
            kg.get_or_materialize()
            kg.invalidate()
            self.assertFalse(kg.status()["cached"])
            _, cached, _ = kg.get_or_materialize()
        self.assertFalse(cached)

    def test_falls_back_without_ontology_and_keeps_the_reason(self):
        def run(cmd, **kwargs):
            if "-t" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="",
                                                   stderr="12:00:00.000 |-ERROR in x - boom happened")
            Path(cmd[cmd.index("-o") + 1]).write_text("<s> <p> <o> .\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=run):
            path, cached, meta = kg.get_or_materialize()
        self.assertFalse(cached)
        self.assertFalse(meta["ontology_applied"])
        self.assertIn("boom happened", meta["ontology_note"])
        self.assertFalse(kg.status()["ontology_applied"])
        self.assertTrue(path.exists())

    def test_ontop_failure_is_reported_without_caching(self):
        calls = []
        run = _fake_materialize(calls, returncode=1, stderr="warn\nERROR: bad mapping line 3")
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=run):
            with self.assertRaises(kg.KnowledgeGraphError) as cm:
                kg.get_or_materialize()
        self.assertIn("bad mapping line 3", str(cm.exception))
        self.assertFalse(kg.status()["cached"])


class KnowledgeGraphPageTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.obda = Path(self._state.name) / "mapping.obda"
        p = mock.patch.object(ontop_process, "OBDA_FILE", self.obda)
        p.start()
        self.addCleanup(p.stop)

    def test_button_is_disabled_without_a_mapping(self):
        self.obda.write_text("[PrefixDeclaration]\n", encoding="utf-8")
        page = Client().get("/map-fields/").content.decode()
        self.assertIn("Download RDF graph", page)
        self.assertNotIn('href="/map-fields/knowledge-graph/"', page)
        self.assertIn("disabled", page)

    def test_button_links_to_the_download_with_a_mapping(self):
        self.obda.write_text(MAPPING_TEXT, encoding="utf-8")
        page = Client().get("/map-fields/").content.decode()
        self.assertIn('href="/map-fields/knowledge-graph/"', page)

    def test_download_returns_turtle_and_second_request_hits_the_cache(self):
        self.obda.write_text(MAPPING_TEXT, encoding="utf-8")
        calls = []
        with mock.patch("myapp.knowledge_graph.subprocess.run", side_effect=_fake_materialize(calls)):
            first = Client().get("/map-fields/knowledge-graph/")
            second = Client().get("/map-fields/knowledge-graph/")
        self.assertEqual(first["Content-Type"], "text/turtle")
        self.assertIn("attachment; filename=\"knowledge-graph-", first["Content-Disposition"])
        self.assertEqual(first["X-HDN-Cache"], "miss")
        self.assertEqual(second["X-HDN-Cache"], "hit")
        self.assertEqual(len(calls), 1)
        self.assertEqual(b"".join(second.streaming_content), b"<s> <p> <o> .\n")

    def test_download_without_mapping_redirects_with_a_message(self):
        self.obda.write_text("[PrefixDeclaration]\n", encoding="utf-8")
        r = Client().get("/map-fields/knowledge-graph/", follow=True)
        self.assertContains(r, "there is no mapping")
