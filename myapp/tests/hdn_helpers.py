"""Supporto ai test dei protocolli di rete: un Central simulato con chiave propria."""
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import override_settings

from myapp import hdnsig, network
from myapp.models import CentralMembership


class FakeCentral:
    URL = "http://central.test/"

    def __init__(self):
        self.identity = hdnsig.Identity(Ed25519PrivateKey.generate())

    def membership(self, status=CentralMembership.APPROVED, url=None):
        return CentralMembership.objects.create(
            central_url=url or self.URL, central_name="Fake Central",
            central_key=self.identity.public_b64, endpoint_name="E",
            endpoint_url="http://endpoint.test:8084/", status=status)

    def post(self, client, path, action, payload, audience=None, body=None, headers=None, now=None):
        """POST firmato verso l'endpoint di test; restituisce la risposta Django."""
        body = body if body is not None else hdnsig.encode_body(payload)
        h = headers or hdnsig.sign_request(
            self.identity, action, audience or network.identity().fingerprint, body, now=now)
        extra = {"HTTP_" + k.upper().replace("-", "_"): v for k, v in h.items() if k != "Content-Type"}
        resp = client.post(path, data=body, content_type="application/json", **extra)
        resp.sent_headers = h
        return resp

    def verify(self, resp, action):
        """Verifica che la risposta sia firmata dalla chiave dell'endpoint."""
        hdnsig.verify_response(network.identity().public_b64, action,
                               resp.sent_headers[hdnsig.H_NONCE], resp.status_code,
                               resp.content, resp)


class StateDirMixin:
    """Directory di stato temporanea per ogni test (identita', catalogo, ontologia)."""

    def setUp(self):
        super().setUp()
        self._state = tempfile.TemporaryDirectory()
        self.addCleanup(self._state.cleanup)
        o = override_settings(HDN_STATE_DIR=self._state.name)
        o.enable()
        self.addCleanup(o.disable)
        network._identity_at.cache_clear()
        self.addCleanup(network._identity_at.cache_clear)
