"""Firma Ed25519 dei messaggi fra HDN Central e HDN Endpoint.

Lo stesso file e' in central-tdn/catalogapp/hdnsig.py e in
tdn-endpoint/myapp/hdnsig.py: le due copie devono restare identiche.

Ogni nodo ha una propria identita', una coppia di chiavi Ed25519 generata al
primo uso e conservata nella sua directory di stato (mai nel repository).

Richiesta firmata (sempre POST con corpo JSON), header:
  X-HDN-Key        chiave pubblica del mittente, base64 dei 32 byte
  X-HDN-Timestamp  secondi Unix
  X-HDN-Nonce      valore casuale, usato una sola volta
  X-HDN-Signature  firma, base64

La firma copre: versione del formato, azione (es. "catalog"), fingerprint del
destinatario, timestamp, nonce e SHA-256 del corpo. L'azione sostituisce il
path HTTP, che un reverse proxy puo' riscrivere; la fingerprint del
destinatario impedisce di riusare verso un altro nodo una richiesta firmata
per questo. Il destinatario rifiuta timestamp fuori finestra e nonce gia' visti.

Anche la risposta e' firmata, sul nonce della richiesta, lo stato HTTP e il
corpo, cosi' che il mittente sappia che ha risposto proprio il nodo atteso.
"""
import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

FORMAT = "HDN-SIG-1"
MAX_SKEW = 300  # secondi
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
ACTION_RE = re.compile(r"[a-z][a-z-]{0,31}")

H_KEY = "X-HDN-Key"
H_TS = "X-HDN-Timestamp"
H_NONCE = "X-HDN-Nonce"
H_SIG = "X-HDN-Signature"


class SignatureError(Exception):
    """Messaggio non autenticato. ``reason`` e' un codice breve per i log."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _unb64(text):
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, AttributeError):
        raise SignatureError("bad-base64")


def public_key(public_b64):
    """Chiave pubblica da base64; SignatureError se non e' una chiave Ed25519."""
    raw = _unb64(public_b64)
    if len(raw) != 32:
        raise SignatureError("bad-key")
    return Ed25519PublicKey.from_public_bytes(raw)


def fingerprint(public_b64):
    """Impronta corta e leggibile di una chiave pubblica, stile OpenSSH."""
    raw = _unb64(public_b64)
    digest = base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")
    return "SHA256:" + digest


class Identity:
    """Chiave privata del nodo."""

    def __init__(self, private_key):
        self._key = private_key
        raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.public_b64 = _b64(raw)
        self.fingerprint = fingerprint(self.public_b64)

    def sign(self, message):
        return _b64(self._key.sign(message))


def load_or_create_identity(path):
    """Identita' salvata in ``path`` (PEM, permessi 0600), creata se manca.

    La creazione usa O_EXCL: se due processi partono insieme, uno scrive la
    chiave e l'altro la legge.
    """
    path = os.fspath(path)
    try:
        with open(path, "rb") as fh:
            return Identity(serialization.load_pem_private_key(fh.read(), password=None))
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_or_create_identity(path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
        fh.flush()
        os.fsync(fh.fileno())
    return Identity(key)


def encode_body(payload):
    """Serializzazione JSON canonica del corpo."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _request_message(action, audience, ts, nonce, body):
    return "\n".join([FORMAT, action, audience, str(ts), nonce,
                      hashlib.sha256(body).hexdigest()]).encode("utf-8")


def _response_message(action, nonce, status, body):
    return "\n".join([FORMAT + "-response", action, nonce, str(status),
                      hashlib.sha256(body).hexdigest()]).encode("utf-8")


def sign_request(identity, action, audience, body, now=None):
    """Header di una richiesta ``action`` firmata per il nodo ``audience``.

    ``audience`` e' la fingerprint della chiave del destinatario.
    """
    if not ACTION_RE.fullmatch(action):
        raise ValueError(f"azione non valida: {action!r}")
    ts = int(now if now is not None else time.time())
    nonce = secrets.token_urlsafe(24)
    return {
        H_KEY: identity.public_b64,
        H_TS: str(ts),
        H_NONCE: nonce,
        H_SIG: identity.sign(_request_message(action, audience, ts, nonce, body)),
        "Content-Type": "application/json",
    }


def verify_request(headers, action, own_fingerprint, body, remember_nonce, now=None):
    """Verifica una richiesta e restituisce la chiave pubblica (base64) del mittente.

    ``headers`` si interroga con ``.get(nome)``. ``remember_nonce(fingerprint,
    nonce)`` registra il nonce e restituisce False se era gia' stato visto.
    Stabilire se quel mittente e' autorizzato spetta al chiamante.
    """
    key_b64, ts, nonce, sig = (headers.get(h) for h in (H_KEY, H_TS, H_NONCE, H_SIG))
    if not all((key_b64, ts, nonce, sig)):
        raise SignatureError("missing-headers")
    if not ts.isdigit():
        raise SignatureError("bad-timestamp")
    if abs(int(now if now is not None else time.time()) - int(ts)) > MAX_SKEW:
        raise SignatureError("stale-timestamp")
    if not NONCE_RE.fullmatch(nonce):
        raise SignatureError("bad-nonce")
    try:
        public_key(key_b64).verify(
            _unb64(sig), _request_message(action, own_fingerprint, int(ts), nonce, body))
    except InvalidSignature:
        raise SignatureError("bad-signature")
    # Il nonce si registra solo dopo la verifica, cosi' che richieste non
    # firmate non riempiano l'archivio dei nonce.
    if not remember_nonce(fingerprint(key_b64), nonce):
        raise SignatureError("replayed-nonce")
    return key_b64


def sign_response(identity, action, request_nonce, status, body):
    """Header della risposta firmata a una richiesta con nonce ``request_nonce``."""
    return {
        H_KEY: identity.public_b64,
        H_SIG: identity.sign(_response_message(action, request_nonce, status, body)),
    }


def verify_response(expected_key_b64, action, request_nonce, status, body, headers):
    """SignatureError se la risposta non e' firmata da ``expected_key_b64``."""
    key_b64, sig = headers.get(H_KEY), headers.get(H_SIG)
    if not key_b64 or not sig:
        raise SignatureError("unsigned-response")
    if key_b64 != expected_key_b64:
        raise SignatureError("unexpected-responder")
    try:
        public_key(key_b64).verify(
            _unb64(sig), _response_message(action, request_nonce, status, body))
    except InvalidSignature:
        raise SignatureError("bad-response-signature")


def post_signed(identity, url, action, audience_key_b64, payload, timeout=30, session=None):
    """POST firmato verso ``url``; restituisce ``(status, dati_json)``.

    La risposta deve essere firmata dalla chiave ``audience_key_b64``,
    altrimenti SignatureError. Errori di rete: ``requests.RequestException``.
    """
    import requests

    body = encode_body(payload)
    headers = sign_request(identity, action, fingerprint(audience_key_b64), body)
    http = session or requests
    resp = http.post(url, data=body, headers=headers, timeout=timeout, allow_redirects=False)
    verify_response(audience_key_b64, action, headers[H_NONCE], resp.status_code,
                    resp.content, resp.headers)
    try:
        data = json.loads(resp.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    return resp.status_code, data
