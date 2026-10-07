"""Audit-test del punto 21: set_level non deve redirigere fuori dall'endpoint.

Prima la view rispondeva ``redirect(request.META['HTTP_REFERER'])``: il Referer
e' scelto dal client, quindi un link o un form su un sito terzo poteva usare
l'endpoint per mandare l'amministratore verso un dominio qualunque.

Il test usa un livello non valido, che non modifica la policy locale.
"""

from django.test import Client, TestCase


class SetLevelRedirectTests(TestCase):

    def _post(self, referer):
        extra = {"HTTP_REFERER": referer} if referer is not None else {}
        return Client().post("/set-level/", {"level": "non-valido"}, **extra)

    def test_external_referer_is_not_followed(self):
        for referer in ["https://evil.example/phish", "//evil.example/x",
                        "http://evil.example", "javascript:alert(1)"]:
            with self.subTest(referer=referer):
                resp = self._post(referer)
                self.assertEqual(resp.status_code, 302)
                self.assertEqual(resp["Location"], "/")

    def test_same_host_referer_is_followed(self):
        resp = self._post("http://testserver/map-fields/")
        self.assertEqual(resp["Location"], "http://testserver/map-fields/")

    def test_missing_referer_goes_home(self):
        self.assertEqual(self._post(None)["Location"], "/")
