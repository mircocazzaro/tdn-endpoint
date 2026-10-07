"""Stato dell'endpoint nella rete HDN: iscrizioni, notifiche, nonce."""
from django.db import models


class CentralMembership(models.Model):
    """Iscrizione dell'endpoint a un HDN Central.

    La chiave di Central e' quella ricevuta al primo contatto e da allora resta
    fissata: un messaggio firmato con un'altra chiave non viene accettato.
    """
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    central_url = models.URLField(unique=True)
    central_name = models.CharField(max_length=100, blank=True)
    central_key = models.CharField(max_length=64)
    # Come questo endpoint si e' presentato a Central
    endpoint_name = models.CharField(max_length=100)
    endpoint_url = models.URLField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    @property
    def central_fingerprint(self):
        from .hdnsig import fingerprint
        return fingerprint(self.central_key)

    def __str__(self):
        return f"{self.central_url} ({self.status})"


class Notification(models.Model):
    """Voce del centro notifiche dell'amministratore."""
    ENROLLMENT = "enrollment"
    CATALOG = "catalog"
    ONTOLOGY = "ontology"
    KIND_CHOICES = [(ENROLLMENT, "Network"), (CATALOG, "Query catalog"), (ONTOLOGY, "Ontology")]

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    LEVEL_CHOICES = [(INFO, "Info"), (SUCCESS, "Success"), (WARNING, "Warning"), (ERROR, "Error")]

    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    level = models.CharField(max_length=8, choices=LEVEL_CHOICES, default=INFO)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created", "-id"]


class SeenNonce(models.Model):
    """Nonce delle richieste firmate gia' accettate (protezione dal replay)."""
    sender = models.CharField(max_length=64)
    nonce = models.CharField(max_length=64)
    seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["sender", "nonce"], name="unique_sender_nonce")]
