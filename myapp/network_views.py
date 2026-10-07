"""Viste della rete HDN.

- /hdn/...: protocolli firmati invocati da HDN Central (porta 8084);
- /network/ e /notifications/: pagine dell'amministratore locale (porta 8000).
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django import forms

from . import catalog, network
from .models import CentralMembership, Notification


# --- protocolli firmati ----------------------------------------------------

@csrf_exempt
def hdn_enrollment(request):
    """Central comunica l'esito della candidatura."""
    return network.handle_signed(
        request, "enrollment", network.enrollment_decision,
        statuses=(CentralMembership.PENDING, CentralMembership.APPROVED, CentralMembership.REJECTED))


@csrf_exempt
def hdn_catalog(request):
    """Central distribuisce un catalogo delle query aggiornato."""
    return network.handle_signed(request, "catalog", network.receive_catalog)


# --- pagine dell'amministratore -------------------------------------------

class ApplyForm(forms.Form):
    central_url = forms.URLField(label="HDN Central URL",
                                 widget=forms.URLInput(attrs={"class": "form-control",
                                                              "placeholder": "https://central.example.org/"}))
    endpoint_url = forms.URLField(label="URL of this endpoint, as Central will reach it",
                                  widget=forms.URLInput(attrs={"class": "form-control",
                                                               "placeholder": "https://endpoint.example.org:8084/"}))
    endpoint_name = forms.CharField(label="Name of this endpoint", max_length=100,
                                    widget=forms.TextInput(attrs={"class": "form-control",
                                                                  "placeholder": "University of ..."}))


@require_http_methods(["GET", "POST"])
def network_view(request):
    form = ApplyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            m = network.apply(**form.cleaned_data)
        except network.EnrollmentError as exc:
            messages.error(request, str(exc))
        else:
            if m.status == CentralMembership.APPROVED:
                messages.success(request, f"{m.central_url} already lists this endpoint.")
            else:
                messages.success(request, f"Application sent to {m.central_url}: "
                                          "waiting for the Central manager to approve it.")
            return redirect("network")
    return render(request, "myapp/network.html", {
        "form": form,
        "memberships": CentralMembership.objects.all(),
        "identity": network.identity(),
        "catalog_version": catalog.active().version,
    })


def notifications_view(request):
    return render(request, "myapp/notifications.html", {
        "notifications": Notification.objects.all()[:200],
    })


@require_POST
def notifications_read(request):
    Notification.objects.filter(read=False).update(read=True)
    return redirect("notifications")
