"""Partecipazione dell'endpoint alla rete HDN.

- identita' del nodo (chiave Ed25519 nella directory di stato);
- verifica delle richieste firmate che arrivano da un Central su /hdn/...;
- candidatura a un Central;
- centro notifiche.

Fiducia nella chiave di Central: si accetta quella ricevuta al primo contatto
(scelta di progetto), poi resta fissata nella CentralMembership. Un Central
che si ripresenta con un'altra chiave allo stesso URL viene rifiutato.
"""
import datetime
import json
import logging
from functools import lru_cache
from pathlib import Path

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone

from . import hdnsig
from .models import CentralMembership, Notification, SeenNonce

audit = logging.getLogger("hdn.audit")

CENTRAL_TIMEOUT = 15
MAX_BODY = 8 * 1024 * 1024  # un'ontologia completa sta abbondantemente sotto


def state_dir():
    return Path(settings.HDN_STATE_DIR)


@lru_cache(maxsize=None)
def _identity_at(path):
    return hdnsig.load_or_create_identity(path)


def identity():
    return _identity_at(str(state_dir() / "identity.pem"))


def notify(kind, title, body="", level=Notification.INFO):
    return Notification.objects.create(kind=kind, title=title, body=body, level=level)


def remember_nonce(sender, nonce):
    """Registra il nonce; False se ``sender`` lo aveva gia' usato."""
    cutoff = timezone.now() - datetime.timedelta(seconds=2 * hdnsig.MAX_SKEW)
    SeenNonce.objects.filter(seen__lt=cutoff).delete()
    try:
        with transaction.atomic():
            SeenNonce.objects.create(sender=sender, nonce=nonce)
    except IntegrityError:
        return False
    return True


class _Headers:
    """Accesso agli header HTTP di una richiesta Django per nome."""

    def __init__(self, request):
        self._r = request

    def get(self, name):
        return self._r.headers.get(name)


def _signed_json(action, nonce, status, payload):
    body = hdnsig.encode_body(payload)
    resp = HttpResponse(body, status=status, content_type="application/json")
    if nonce:
        for k, v in hdnsig.sign_response(identity(), action, nonce, status, body).items():
            resp[k] = v
    return resp


def handle_signed(request, action, handler, statuses=(CentralMembership.APPROVED,)):
    """Esegue ``handler(membership, payload)`` se la richiesta e' firmata da un Central noto.

    ``statuses`` sono gli stati di iscrizione ammessi per quell'azione.
    ``handler`` restituisce ``(status_http, dict)``; la risposta e' firmata.
    Richieste non autenticate ricevono 401/403 non firmate e senza dettagli.
    """
    if request.method != "POST":
        return HttpResponse(status=405)
    body = request.body
    if len(body) > MAX_BODY:
        return HttpResponse(status=413)
    try:
        sender = hdnsig.verify_request(_Headers(request), action, identity().fingerprint,
                                       body, remember_nonce)
    except hdnsig.SignatureError as exc:
        audit.warning("hdn-reject action=%s reason=%s remote=%s",
                      action, exc.reason, request.META.get("REMOTE_ADDR", "-"))
        return HttpResponse(status=401)
    nonce = request.headers.get(hdnsig.H_NONCE)
    membership = CentralMembership.objects.filter(central_key=sender, status__in=statuses).first()
    if membership is None:
        audit.warning("hdn-reject action=%s reason=unknown-central key=%s",
                      action, hdnsig.fingerprint(sender))
        return _signed_json(action, nonce, 403, {"error": "not a member of this central"})
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        return _signed_json(action, nonce, 400, {"error": "body must be a JSON object"})
    status, data = handler(membership, payload)
    audit.info("hdn-%s central=%s status=%s", action, membership.central_url, status)
    return _signed_json(action, nonce, status, data)


# ---------------------------------------------------------------------------
# Candidatura
# ---------------------------------------------------------------------------

class EnrollmentError(Exception):
    pass


def _base(url):
    return url.rstrip("/") + "/"


def apply(central_url, endpoint_url, endpoint_name):
    """Candida l'endpoint a ``central_url``; restituisce la CentralMembership.

    ``endpoint_url`` e' l'indirizzo da cui Central raggiunge l'endpoint (la
    porta 8084), lo stesso che Central usera' per le query.
    """
    central_url = _base(central_url)
    try:
        r = requests.get(central_url + "hdn/key/", timeout=CENTRAL_TIMEOUT, allow_redirects=False)
        r.raise_for_status()
        info = r.json()
        central_key = info["public_key"]
        hdnsig.public_key(central_key)
    except (requests.RequestException, ValueError, KeyError, TypeError, hdnsig.SignatureError) as exc:
        raise EnrollmentError(f"{central_url} did not provide an HDN Central key ({type(exc).__name__}).")

    existing = CentralMembership.objects.filter(central_url=central_url).first()
    if existing and existing.central_key != central_key:
        audit.warning("hdn-central-key-changed url=%s old=%s new=%s", central_url,
                      existing.central_fingerprint, hdnsig.fingerprint(central_key))
        raise EnrollmentError(
            f"The key of {central_url} changed since the first contact "
            f"({existing.central_fingerprint} -> {hdnsig.fingerprint(central_key)}). "
            "Enrollment refused: delete the old membership only if the change is expected.")

    payload = {"name": endpoint_name, "url": endpoint_url, "public_key": identity().public_b64}
    try:
        status, data = hdnsig.post_signed(identity(), central_url + "hdn/enroll/", "enroll",
                                          central_key, payload, timeout=CENTRAL_TIMEOUT)
    except hdnsig.SignatureError as exc:
        raise EnrollmentError(f"Central answered without a valid signature ({exc.reason}).")
    except requests.RequestException as exc:
        raise EnrollmentError(f"Central is not reachable ({type(exc).__name__}).")
    if status not in (200, 202) or not isinstance(data, dict):
        detail = data.get("error") if isinstance(data, dict) else ""
        raise EnrollmentError(f"Central refused the application (HTTP {status}) {detail}".strip())

    new_status = (CentralMembership.APPROVED if data.get("status") == "approved"
                  else CentralMembership.PENDING)
    membership, _ = CentralMembership.objects.update_or_create(
        central_url=central_url,
        defaults=dict(central_key=central_key, central_name=str(info.get("name", ""))[:100],
                      endpoint_name=endpoint_name, endpoint_url=endpoint_url, status=new_status),
    )
    notify(Notification.ENROLLMENT, f"Application sent to {central_url}",
           f"Central key {membership.central_fingerprint}. Waiting for approval."
           if new_status == CentralMembership.PENDING else "Already approved.")
    return membership


def enrollment_decision(membership, payload):
    """Esito della candidatura comunicato da Central (azione ``enrollment``)."""
    decision = payload.get("decision")
    if decision not in ("approved", "rejected"):
        return 400, {"error": "decision must be approved or rejected"}
    name = str(payload.get("central_name") or membership.central_name)[:100]
    membership.central_name = name
    if decision == "approved":
        membership.status = CentralMembership.APPROVED
        notify(Notification.ENROLLMENT, f"You joined the network of {name or membership.central_url}",
               f"Central {membership.central_url} approved this endpoint "
               f"(key {membership.central_fingerprint}). It can now send queries, "
               "query catalogs and ontologies.", Notification.SUCCESS)
    else:
        membership.status = CentralMembership.REJECTED
        notify(Notification.ENROLLMENT, f"{name or membership.central_url} rejected the application",
               "", Notification.WARNING)
    membership.save()
    return 200, {"status": membership.status, "public_key": identity().public_b64}


# ---------------------------------------------------------------------------
# Catalogo delle query
# ---------------------------------------------------------------------------

def _catalog_changes(old, new):
    added = sorted(set(new.by_key) - set(old.by_key))
    removed = sorted(set(old.by_key) - set(new.by_key))
    changed = sorted(k for k in set(old.by_key) & set(new.by_key)
                     if (old.by_key[k].sha512, old.by_key[k].level,
                         dict(old.by_key[k].params)) !=
                        (new.by_key[k].sha512, new.by_key[k].level,
                         dict(new.by_key[k].params)))
    return added, removed, changed


def receive_catalog(membership, payload):
    """Catalogo distribuito da Central (azione ``catalog``): installato subito."""
    from . import catalog

    doc = payload.get("catalog")
    current = catalog.active()
    try:
        candidate = catalog.from_document(doc)
    except catalog.CatalogIntegrityError as exc:
        notify(Notification.CATALOG,
               f"Refused a query catalog from {membership.central_name or membership.central_url}",
               f"The catalog is not valid:\n{exc}", Notification.ERROR)
        return 422, {"error": "invalid catalog", "problems": str(exc).splitlines()[:50]}

    if candidate.version <= current.version:
        if (candidate.version == current.version
                and candidate.content_digest() == current.content_digest()):
            return 200, {"status": "current", "version": current.version}
        return 409, {"error": "stale catalog", "installed": current.version}

    new, old = catalog.install(doc)
    added, removed, changed = _catalog_changes(old, new)
    lines = [f"{len(new.templates)} queries (was {len(old.templates)})."]
    for label, keys in (("Added", added), ("Removed", removed), ("Changed", changed)):
        if keys:
            lines.append(f"{label}: {', '.join(keys)}")
    notify(Notification.CATALOG,
           f"New query catalog v{new.version} from {membership.central_name or membership.central_url}",
           "\n".join(lines), Notification.INFO)
    return 200, {"status": "installed", "version": new.version}
