"""Accesso all'interfaccia di amministrazione: login, primo avvio, utenti locali.

Tutti gli utenti locali sono amministratori: non ci sono ruoli. Le password
seguono i validatori di Django (AUTH_PASSWORD_VALIDATORS). Dopo troppi tentativi
falliti per lo stesso nome utente l'accesso e' sospeso per un periodo; il
contatore sta nella cache su file, condivisa fra i processi.
"""
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

LOGIN_LIMIT = 5                # tentativi falliti prima della sospensione
LOGIN_WINDOW = 15 * 60         # secondi: durata della sospensione
GENERIC_FAILURE = "Invalid username or password."
USERNAME_RE = re.compile(r"^[\w.@+-]{3,150}$")
# Password dell'utente di default (migrazione 0002): finche' e' in uso l'interfaccia mostra un avviso.
DEFAULT_PASSWORD = "hereditary"
SESSION_FLAG = "default_password"


def _User():
    return get_user_model()


def _fail_key(username):
    return "login-fail:" + username.strip().lower()


def _suspended(username):
    return cache.get(_fail_key(username), 0) >= LOGIN_LIMIT


def _record_failure(username):
    key = _fail_key(username)
    cache.add(key, 0, LOGIN_WINDOW)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, LOGIN_WINDOW)


def _safe_next(request, default="/"):
    target = request.POST.get("next") or request.GET.get("next") or default
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=False):
        return target
    return default


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")
    error = None
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        if _suspended(username):
            error = "Too many failed attempts. Wait a few minutes and try again."
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                _record_failure(username)
                error = GENERIC_FAILURE
            else:
                cache.delete(_fail_key(username))
                login(request, user)
                request.session[SESSION_FLAG] = user.check_password(DEFAULT_PASSWORD)
                return redirect(_safe_next(request))
    return render(request, "myapp/login.html", {
        "error": error, "username": username, "next": request.GET.get("next", ""),
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


@require_http_methods(["GET", "POST"])
def setup_view(request):
    """Primo avvio: crea il primo amministratore. Non disponibile dopo, quando esiste un utente."""
    if _User().objects.exists():
        return redirect("login")
    error = None
    username = ""
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password1", "")
        error = _check_new_user(username, password, request.POST.get("password2", ""))
        if error is None:
            user = _User().objects.create_user(username=username, password=password, is_staff=True)
            login(request, user)
            return redirect("/")
    return render(request, "myapp/setup.html", {"error": error, "username": username})


def _check_new_user(username, password, confirm):
    """Messaggio d'errore per un nuovo utente, o None se e' valido."""
    if not USERNAME_RE.match(username):
        return "The username must be 3 to 150 characters: letters, digits and . @ + - _"
    if _User().objects.filter(username__iexact=username).exists():
        return "A user with this name already exists."
    if password != confirm:
        return "The two passwords do not match."
    try:
        validate_password(password, user=_User()(username=username))
    except ValidationError as exc:
        return " ".join(exc.messages)
    return None


@require_http_methods(["GET", "POST"])
def users_view(request):
    """Utenti locali: creazione, cambio della propria password, reset e rimozione degli altri."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            username = request.POST.get("username", "").strip()
            error = _check_new_user(username, request.POST.get("password1", ""),
                                    request.POST.get("password2", ""))
            if error:
                messages.error(request, error)
            else:
                _User().objects.create_user(username=username, password=request.POST["password1"],
                                            is_staff=True)
                messages.success(request, f"User {username} created.")
        elif action == "change_own":
            _change_own(request)
        elif action == "reset":
            _reset_other(request)
        elif action == "delete":
            _delete_other(request)
        else:
            messages.error(request, "Unknown action.")
        return redirect("users")

    users = _User().objects.order_by("username")
    return render(request, "myapp/users.html", {"users": users, "me": request.user})


def _change_own(request):
    user = request.user
    if not user.check_password(request.POST.get("current", "")):
        messages.error(request, "The current password is wrong.")
        return
    new, confirm = request.POST.get("password1", ""), request.POST.get("password2", "")
    if new != confirm:
        messages.error(request, "The two passwords do not match.")
        return
    try:
        validate_password(new, user=user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return
    user.set_password(new)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)  # questa sessione resta aperta, le altre no
    request.session[SESSION_FLAG] = False
    messages.success(request, "Your password has been changed. Other sessions were closed.")


def _target(request):
    try:
        return _User().objects.get(pk=int(request.POST.get("user_id", "")))
    except (ValueError, _User().DoesNotExist):
        messages.error(request, "Unknown user.")
        return None


def _reset_other(request):
    target = _target(request)
    if target is None:
        return
    new, confirm = request.POST.get("password1", ""), request.POST.get("password2", "")
    if new != confirm:
        messages.error(request, "The two passwords do not match.")
        return
    try:
        validate_password(new, user=target)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return
    target.set_password(new)
    target.save(update_fields=["password"])
    messages.success(request, f"Password of {target.username} changed. Its sessions were closed.")


def _delete_other(request):
    target = _target(request)
    if target is None:
        return
    if target.pk == request.user.pk:
        messages.error(request, "You cannot delete the account you are using.")
        return
    if _User().objects.count() <= 1:
        messages.error(request, "You cannot delete the last user.")
        return
    name = target.username
    target.delete()
    messages.success(request, f"User {name} deleted.")
