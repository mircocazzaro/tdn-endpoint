"""Firma dei messaggi di rete e protocollo di iscrizione a un Central."""
import json
import os
import stat
import time
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from myapp import hdnsig, network
from myapp.models import CentralMembership, Notification

from .hdn_helpers import FakeCentral, StateDirMixin


class SignatureTests(SimpleTestCase):
    def setUp(self):
        self.a = FakeCentral().identity
        self.b = FakeCentral().identity
        self.seen = set()

    def remember(self, sender, nonce):
        if (sender, nonce) in self.seen:
            return False
        self.seen.add((sender, nonce))
        return True

    def verify(self, headers, body, action="catalog", audience=None, now=None):
        return hdnsig.verify_request(headers, action, audience or self.b.fingerprint, body,
                                     self.remember, now=now)

    def test_valid_request_returns_sender_key(self):
        body = b'{"x":1}'
        h = hdnsig.sign_request(self.a, "catalog", self.b.fingerprint, body)
        self.assertEqual(self.verify(h, body), self.a.public_b64)

    def test_rejections(self):
        body = b'{"x":1}'
        h = hdnsig.sign_request(self.a, "catalog", self.b.fingerprint, body)
        cases = {
            "bad-signature": lambda: self.verify(h, b'{"x":2}'),                  # corpo alterato
            "bad-signature ": lambda: self.verify(h, body, action="ontology"),    # altra azione
            "bad-signature  ": lambda: self.verify(h, body, audience=self.a.fingerprint),  # altro destinatario
            "stale-timestamp": lambda: self.verify(h, body, now=time.time() + 3600),
            "missing-headers": lambda: self.verify({}, body),
        }
        for reason, call in cases.items():
            with self.subTest(reason):
                with self.assertRaises(hdnsig.SignatureError) as cm:
                    call()
                self.assertEqual(cm.exception.reason, reason.strip())

    def test_replay_rejected(self):
        body = b"{}"
        h = hdnsig.sign_request(self.a, "catalog", self.b.fingerprint, body)
        self.verify(h, body)
        with self.assertRaises(hdnsig.SignatureError) as cm:
            self.verify(h, body)
        self.assertEqual(cm.exception.reason, "replayed-nonce")

    def test_forged_key_header_rejected(self):
        body = b"{}"
        h = hdnsig.sign_request(self.a, "catalog", self.b.fingerprint, body)
        h[hdnsig.H_KEY] = self.b.public_b64
        with self.assertRaises(hdnsig.SignatureError):
            self.verify(h, body)

    def test_response_signature(self):
        hs = hdnsig.sign_response(self.a, "catalog", "n" * 20, 200, b"{}")
        hdnsig.verify_response(self.a.public_b64, "catalog", "n" * 20, 200, b"{}", hs)
        for args in [(self.b.public_b64, "catalog", "n" * 20, 200, b"{}"),   # altro firmatario
                     (self.a.public_b64, "catalog", "m" * 20, 200, b"{}"),   # altra richiesta
                     (self.a.public_b64, "catalog", "n" * 20, 403, b"{}")]:  # altro stato
            with self.assertRaises(hdnsig.SignatureError):
                hdnsig.verify_response(*args, hs)


class IdentityTests(StateDirMixin, SimpleTestCase):
    def test_key_created_once_private_and_stable(self):
        a = network.identity()
        path = os.path.join(self._state.name, "identity.pem")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        network._identity_at.cache_clear()
        self.assertEqual(network.identity().public_b64, a.public_b64)


class EnrollmentDecisionTests(StateDirMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.central = FakeCentral()
        self.client = Client()

    def test_approval_marks_member_notifies_and_signs_answer(self):
        self.central.membership(status=CentralMembership.PENDING)
        resp = self.central.post(self.client, "/hdn/enrollment/", "enrollment",
                                 {"decision": "approved", "central_name": "Fake Central"})
        self.assertEqual(resp.status_code, 200)
        self.central.verify(resp, "enrollment")
        self.assertEqual(json.loads(resp.content)["public_key"], network.identity().public_b64)
        self.assertEqual(CentralMembership.objects.get().status, CentralMembership.APPROVED)
        n = Notification.objects.get()
        self.assertEqual((n.kind, n.level), (Notification.ENROLLMENT, Notification.SUCCESS))
        self.assertIn("joined the network of Fake Central", n.title)

    def test_unknown_central_refused(self):
        resp = self.central.post(self.client, "/hdn/enrollment/", "enrollment", {"decision": "approved"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Notification.objects.exists())

    def test_unsigned_tampered_and_replayed_refused(self):
        self.central.membership(status=CentralMembership.PENDING)
        self.assertEqual(self.client.post("/hdn/enrollment/", data="{}",
                                          content_type="application/json").status_code, 401)
        good = self.central.post(self.client, "/hdn/enrollment/", "enrollment", {"decision": "rejected"})
        self.assertEqual(good.status_code, 200)
        replay = self.central.post(self.client, "/hdn/enrollment/", "enrollment", {},
                                   body=hdnsig.encode_body({"decision": "rejected"}),
                                   headers=good.sent_headers)
        self.assertEqual(replay.status_code, 401)
        h = hdnsig.sign_request(self.central.identity, "enrollment", network.identity().fingerprint,
                                hdnsig.encode_body({"decision": "rejected"}))
        tampered = self.central.post(self.client, "/hdn/enrollment/", "enrollment", {},
                                     body=hdnsig.encode_body({"decision": "approved"}), headers=h)
        self.assertEqual(tampered.status_code, 401)
        self.assertEqual(CentralMembership.objects.get().status, CentralMembership.REJECTED)

    def test_signed_for_another_endpoint_refused(self):
        self.central.membership(status=CentralMembership.PENDING)
        other = FakeCentral().identity.fingerprint
        resp = self.central.post(self.client, "/hdn/enrollment/", "enrollment",
                                 {"decision": "approved"}, audience=other)
        self.assertEqual(resp.status_code, 401)


class ApplyTests(StateDirMixin, TestCase):
    """Candidatura: Central e' simulato a livello di ``requests``."""

    def setUp(self):
        super().setUp()
        self.central = FakeCentral()
        self.sent = []

    def fake_get(self, url, **kw):
        r = mock.Mock(status_code=200)
        r.raise_for_status = lambda: None
        r.json = lambda: {"name": "Fake Central", "public_key": self.central.identity.public_b64}
        return r

    def fake_post(self, url, data, headers, **kw):
        sender = hdnsig.verify_request(headers, "enroll", self.central.identity.fingerprint,
                                       data, lambda s, n: True)
        self.sent.append((url, json.loads(data), sender))
        body = hdnsig.encode_body({"status": "pending"})
        h = hdnsig.sign_response(self.central.identity, "enroll", headers[hdnsig.H_NONCE], 202, body)
        return mock.Mock(status_code=202, content=body, headers=h)

    def apply(self):
        with mock.patch("requests.get", self.fake_get), mock.patch("requests.post", self.fake_post):
            return network.apply("http://central.test", "http://endpoint.test:8084/", "Endpoint X")

    def test_apply_sends_signed_application_and_pins_key(self):
        m = self.apply()
        url, payload, sender = self.sent[0]
        self.assertEqual(url, "http://central.test/hdn/enroll/")
        self.assertEqual(payload["public_key"], network.identity().public_b64)
        self.assertEqual(sender, network.identity().public_b64)
        self.assertEqual((m.status, m.central_key), (CentralMembership.PENDING, self.central.identity.public_b64))
        self.assertTrue(Notification.objects.filter(kind=Notification.ENROLLMENT).exists())

    def test_changed_central_key_refused(self):
        self.apply()
        self.central = FakeCentral()  # stesso URL, chiave diversa
        with self.assertRaises(network.EnrollmentError) as cm:
            self.apply()
        self.assertIn("changed since the first contact", str(cm.exception))
        self.assertEqual(len(self.sent), 1)

    def test_unsigned_answer_refused(self):
        def unsigned_post(url, data, headers, **kw):
            return mock.Mock(status_code=202, content=b'{"status":"pending"}', headers={})
        with mock.patch("requests.get", self.fake_get), mock.patch("requests.post", unsigned_post):
            with self.assertRaises(network.EnrollmentError):
                network.apply("http://central.test", "http://endpoint.test:8084/", "E")
        self.assertFalse(CentralMembership.objects.exists())


class PortSeparationTests(SimpleTestCase):
    def test_8084_exposes_hdn_but_not_admin_pages(self):
        from myproject import sparql_wsgi
        calls = []
        with mock.patch.object(sparql_wsgi, "_django", lambda env, sr: calls.append(env["PATH_INFO"]) or [b""]):
            for path in ("/hdn/catalog/", "/network/", "/notifications/"):
                sparql_wsgi.application({"PATH_INFO": path}, lambda *a: None)
        self.assertEqual(calls, ["/hdn/catalog/"])
