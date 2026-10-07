"""Protocollo di aggiornamento del catalogo delle query."""
import copy
import json
import os

from django.test import Client, TestCase

from myapp import catalog
from myapp.models import CentralMembership, Notification

from .helpers import central_request
from .hdn_helpers import FakeCentral, StateDirMixin


def base_doc(version=1):
    doc = catalog.BASE.to_document()
    doc["version"] = version
    return doc


class CatalogDocumentTests(TestCase):
    def test_base_roundtrip(self):
        cat = catalog.from_document(base_doc())
        self.assertEqual([t.key for t in cat.templates], [t.key for t in catalog.CATALOG])
        self.assertEqual(cat.content_digest(), catalog.BASE.content_digest())

    def test_invalid_documents_rejected(self):
        def mutate(f):
            d = base_doc()
            f(d)
            return d
        cases = {
            "unknown grammar": mutate(lambda d: d["templates"][0]["params"].update(disease="free_text")),
            "hash mismatch": mutate(lambda d: d["templates"][0].update(sparql=d["templates"][0]["sparql"] + " ")),
            "undeclared placeholder": mutate(lambda d: d["templates"][0].update(
                sparql="ASK { ?s ?p {x} }", sha512=None) or d["templates"][0].pop("sha512")),
            "bad level": mutate(lambda d: d["templates"][0].update(level=9)),
            "duplicate key": mutate(lambda d: d["templates"].append(copy.deepcopy(d["templates"][0]))),
            "prologue with SPARQL": mutate(lambda d: d.update(prologue="PREFIX a: <x:>\nSELECT * {}")),
            "version zero": base_doc(version=0),
            "no templates": mutate(lambda d: d.update(templates=[])),
        }
        for label, doc in cases.items():
            with self.subTest(label):
                with self.assertRaises(catalog.CatalogIntegrityError):
                    catalog.from_document(doc)


class CatalogProtocolTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.central = FakeCentral()
        self.central.membership()
        self.client = Client()

    def send(self, doc):
        return self.central.post(self.client, "/hdn/catalog/", "catalog", {"catalog": doc})

    def test_new_catalog_installed_used_and_notified(self):
        doc = base_doc(1)
        doc["templates"] = [t for t in doc["templates"] if t["key"] != "q02_L1"]
        doc["templates"][0]["level"] = 6  # q00_L0 diventa L6
        resp = self.send(doc)
        self.assertEqual(resp.status_code, 200)
        self.central.verify(resp, "catalog")
        self.assertEqual(json.loads(resp.content), {"status": "installed", "version": 1})
        self.assertTrue(os.path.exists(os.path.join(self._state.name, "catalog.json")))

        active = catalog.active()
        self.assertEqual(active.version, 1)
        with self.assertRaises(catalog.RejectedQuery):
            catalog.match_query(central_request("q02_L1", disease="NCIT:C34373")["query"])
        self.assertEqual(catalog.match_query(
            central_request("q00_L0", disease="NCIT:C34373")["query"]).template.level, 6)

        n = Notification.objects.get(kind=Notification.CATALOG)
        self.assertIn("New query catalog v1 from Fake Central", n.title)
        self.assertIn("Removed: q02_L1", n.body)
        self.assertIn("Changed: q00_L0", n.body)

    def test_same_version_is_idempotent_older_is_refused(self):
        self.assertEqual(self.send(base_doc(2)).status_code, 200)
        again = self.send(base_doc(2))
        self.assertEqual((again.status_code, json.loads(again.content)["status"]), (200, "current"))
        changed = base_doc(2)
        changed["templates"].pop()
        self.assertEqual(self.send(changed).status_code, 409)
        old = self.send(base_doc(1))
        self.assertEqual((old.status_code, json.loads(old.content)["installed"]), (409, 2))
        self.assertEqual(catalog.active().version, 2)

    def test_invalid_catalog_refused_and_notified(self):
        doc = base_doc(1)
        doc["templates"][0]["params"]["disease"] = "free_text"
        resp = self.send(doc)
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(catalog.active().version, 0)
        self.assertEqual(Notification.objects.get().level, Notification.ERROR)

    def test_only_approved_centrals(self):
        other = FakeCentral()
        other.membership(status=CentralMembership.PENDING, url="http://other.test/")
        resp = other.post(self.client, "/hdn/catalog/", "catalog", {"catalog": base_doc(1)})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(catalog.active().version, 0)

    def test_corrupted_file_falls_back_to_base(self):
        self.send(base_doc(1))
        with open(os.path.join(self._state.name, "catalog.json"), "a") as fh:
            fh.write("garbage")
        with self.assertLogs("hdn.audit", "ERROR"):
            self.assertIs(catalog.active(), catalog.BASE)
