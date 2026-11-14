"""Caricamento locale di un'ontologia Turtle dalla pagina /ontology/."""
import tempfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from .helpers import logged_in_client

from myapp import ontology, ontop_process
from myapp.models import Notification

from .hdn_helpers import StateDirMixin
from .test_hdn_ontology import MAPPING, MIN_TEMPLATE, NEW_TTL, OLD_TTL


def ttl_file(text, name="new.ttl"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/turtle")


def obda_file(text, name="template.obda"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/plain")


class OntologyUploadTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = logged_in_client()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        base_ttl = root / "base.ttl"
        base_ttl.write_text(OLD_TTL, encoding="utf-8")
        self.obda = root / "active.obda"
        self.obda.write_text(MAPPING, encoding="utf-8")
        for name, value in (("TTL_FILE", base_ttl), ("OBDA_FILE", self.obda)):
            p = mock.patch.object(ontop_process, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(ontop_process, "is_running", lambda: False)
        p.start()
        self.addCleanup(p.stop)

    def upload(self, ttl, template=None, name="new.ttl"):
        data = {"ontology_file": ttl_file(ttl, name)}
        if template is not None:
            data["mapping_file"] = obda_file(template)
        return self.client.post("/ontology/", data, follow=True)

    def test_page_shows_installed_version(self):
        resp = self.client.get("/ontology/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "v0")
        self.assertContains(resp, "Upload a new ontology")

    def test_upload_installs_ontology_with_its_template(self):
        resp = self.upload(NEW_TTL, MIN_TEMPLATE)
        self.assertContains(resp, "Ontology v1 installed")
        self.assertEqual(ontology.installed()["version"], 1)
        self.assertEqual(ontology.active_path().read_text(encoding="utf-8"), NEW_TTL)
        self.assertEqual(Path(ontology.template_path("unused")).read_text(encoding="utf-8"),
                         MIN_TEMPLATE)
        # MAPPING usava bto:ageOnset, che la nuova ontologia non dichiara
        self.assertNotIn("MAPID-ONSET", self.obda.read_text(encoding="utf-8"))
        n = Notification.objects.get(kind=Notification.ONTOLOGY)
        self.assertEqual(n.title, "New ontology v1 from local upload new.ttl")
        self.assertIn("MAPID-ONSET", n.body)

    def test_second_upload_without_template_keeps_installed_template(self):
        self.assertEqual(self.upload(NEW_TTL, MIN_TEMPLATE).status_code, 200)
        newer = NEW_TTL + "bto:Extra a owl:Class .\n"
        resp = self.upload(newer, name="newer.ttl")
        self.assertContains(resp, "Ontology v2 installed")
        self.assertEqual(ontology.installed()["version"], 2)
        self.assertEqual(ontology.active_path().read_text(encoding="utf-8"), newer)
        self.assertEqual(Path(ontology.template_path("unused")).read_text(encoding="utf-8"),
                         MIN_TEMPLATE)
        # la versione precedente resta fra le copie
        self.assertTrue((Path(self._state.name) / "ontology" / "backups" / "v1.ttl").is_file())

    def test_template_missing_terms_rejects_upload_and_changes_nothing(self):
        # Senza template caricato si usa quello del repository, che usa termini
        # che NEW_TTL non dichiara: l'aggiornamento deve essere rifiutato.
        resp = self.upload(NEW_TTL)
        self.assertContains(resp, "Ontology rejected, nothing was changed")
        self.assertEqual(ontology.installed()["version"], 0)
        self.assertEqual(ontology.active_path(), Path(ontop_process.TTL_FILE))
        self.assertEqual(self.obda.read_text(encoding="utf-8"), MAPPING)

    def test_invalid_turtle_is_rejected(self):
        resp = self.upload("this is not turtle {", MIN_TEMPLATE)
        self.assertContains(resp, "Ontology rejected, nothing was changed")
        self.assertEqual(ontology.installed()["version"], 0)
        self.assertEqual(self.obda.read_text(encoding="utf-8"), MAPPING)

    def test_non_turtle_extension_is_refused_by_the_form(self):
        resp = self.client.post("/ontology/", {
            "ontology_file": ttl_file(NEW_TTL, "ontology.owl")})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "must be a Turtle file")
        self.assertEqual(ontology.installed()["version"], 0)

    def test_template_must_be_obda(self):
        resp = self.client.post("/ontology/", {
            "ontology_file": ttl_file(NEW_TTL),
            "mapping_file": obda_file(MIN_TEMPLATE, "template.txt")})
        self.assertContains(resp, "must be an .obda file")
        self.assertEqual(ontology.installed()["version"], 0)

    def test_ontology_page_is_linked_from_navigation(self):
        self.assertContains(self.client.get("/ontology/"), 'href="/ontology/"')
