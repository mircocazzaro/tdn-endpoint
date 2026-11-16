"""Login sull'interfaccia di amministrazione, primo avvio e utenti locali."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from myapp import accounts
from myapp.middleware import is_public

from .hdn_helpers import StateDirMixin
from .helpers import logged_in_client

PASSWORD = "correct-horse-7"


def make_user(username="ana", password=PASSWORD):
    return get_user_model().objects.create_user(username=username, password=password, is_staff=True)


class ProtectedPagesTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        make_user()
        cache.clear()

    def test_admin_pages_redirect_to_login_when_anonymous(self):
        c = Client()
        for path in ("/", "/map-fields/", "/query/", "/sparql/", "/data/", "/galois/",
                     "/settings/", "/settings/users/", "/ontology/", "/network/"):
            r = c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertTrue(r["Location"].startswith("/login/?next="), (path, r["Location"]))

    def test_posts_are_refused_when_anonymous(self):
        c = Client()
        for path in ("/set-level/", "/data/", "/settings/users/", "/query/"):
            r = c.post(path, {"x": "1"})
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/login/", r["Location"])

    def test_the_django_admin_is_protected_too(self):
        r = Client().get("/admin/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r["Location"])

    def test_public_paths_stay_reachable_without_login(self):
        c = Client()
        self.assertEqual(c.get("/ontop/status/").status_code, 200, "health check del container")
        self.assertEqual(c.get("/login/").status_code, 200)
        self.assertEqual(c.get("/static/myapp/logo.png").status_code, 200)

    def test_central_protocols_are_not_behind_the_login(self):
        """/hdn/ e /sparql-protected/ hanno i propri controlli: il login non cambia nulla per Central."""
        self.assertTrue(is_public("/hdn/catalog/"))
        self.assertTrue(is_public("/hdn/enrollment/"))
        self.assertTrue(is_public("/sparql-protected/"))
        self.assertFalse(is_public("/settings/"))
        self.assertFalse(is_public("/admin/"))
        self.assertFalse(is_public("/loginx/"), "il prefisso deve finire con la barra")
        r = Client().post("/sparql-protected/", {"query": "SELECT 1"})
        self.assertNotEqual(r.status_code, 302, "il protocollo non deve reindirizzare al login")

    def test_authenticated_user_gets_the_pages(self):
        c = Client()
        self.assertTrue(c.login(username="ana", password=PASSWORD))
        self.assertEqual(c.get("/settings/users/").status_code, 200)


class LoginTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        make_user()
        cache.clear()

    def login(self, username="ana", password=PASSWORD, **extra):
        return Client().post("/login/", dict({"username": username, "password": password}, **extra))

    def test_success_redirects_and_opens_a_session(self):
        r = self.login(next="/settings/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "/settings/")

    def test_next_is_only_followed_on_this_host(self):
        for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            r = self.login(next=target)
            self.assertEqual(r["Location"], "/", target)

    def test_wrong_password_and_unknown_user_give_the_same_message(self):
        a = self.login(password="wrong-password-1")
        b = self.login(username="nobody", password="wrong-password-1")
        self.assertContains(a, accounts.GENERIC_FAILURE)
        self.assertContains(b, accounts.GENERIC_FAILURE)
        self.assertEqual(a.status_code, b.status_code)

    def test_too_many_failures_suspend_the_username(self):
        for _ in range(accounts.LOGIN_LIMIT):
            self.login(password="wrong-password-1")
        r = self.login()  # la password giusta non basta durante la sospensione
        self.assertContains(r, "Too many failed attempts")
        from django.conf import settings
        self.assertNotIn(settings.SESSION_COOKIE_NAME, r.cookies)

    def test_a_success_clears_the_counter(self):
        for _ in range(accounts.LOGIN_LIMIT - 1):
            self.login(password="wrong-password-1")
        self.assertEqual(self.login().status_code, 302)
        for _ in range(accounts.LOGIN_LIMIT - 1):
            self.login(password="wrong-password-1")
        self.assertEqual(self.login().status_code, 302, "il contatore va azzerato dal successo")

    def test_username_case_does_not_bypass_the_limit(self):
        for _ in range(accounts.LOGIN_LIMIT):
            self.login(username="ANA", password="wrong-password-1")
        self.assertContains(self.login(username="Ana"), "Too many failed attempts")

    def test_logout_needs_post_and_ends_the_session(self):
        c = Client()
        c.login(username="ana", password=PASSWORD)
        self.assertEqual(c.get("/logout/").status_code, 405)
        r = c.post("/logout/")
        self.assertEqual(r["Location"], "/login/")
        self.assertEqual(c.get("/settings/users/").status_code, 302)

    def test_a_signed_in_user_is_sent_away_from_the_login_page(self):
        c = Client()
        c.login(username="ana", password=PASSWORD)
        self.assertEqual(c.get("/login/")["Location"], "/")


class FirstStartTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        # l'utente di default esiste gia' dopo le migrazioni: il primo avvio parte da zero
        get_user_model().objects.all().delete()

    def test_without_users_every_page_goes_to_setup(self):
        c = Client()
        self.assertEqual(c.get("/")["Location"], "/setup/")
        self.assertEqual(c.get("/settings/")["Location"], "/setup/")

    def test_setup_creates_the_first_administrator_and_signs_in(self):
        c = Client()
        r = c.post("/setup/", {"username": "admin", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(r["Location"], "/")
        self.assertTrue(get_user_model().objects.get(username="admin").is_staff)
        self.assertEqual(c.get("/settings/users/").status_code, 200)

    def test_setup_is_closed_after_the_first_user(self):
        make_user()
        r = Client().get("/setup/")
        self.assertEqual(r["Location"], "/login/")
        r = Client().post("/setup/", {"username": "intruder", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(r["Location"], "/login/")
        self.assertFalse(get_user_model().objects.filter(username="intruder").exists())

    def test_setup_checks_the_password(self):
        r = Client().post("/setup/", {"username": "admin", "password1": "short", "password2": "short"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "at least 10 characters")
        r = Client().post("/setup/", {"username": "admin", "password1": PASSWORD, "password2": "other-one-9"})
        self.assertContains(r, "do not match")


class UsersPageTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        get_user_model().objects.filter(username="admin").delete()
        self.ana = make_user("ana")
        self.client = Client()
        self.client.login(username="ana", password=PASSWORD)

    def post(self, **data):
        return self.client.post("/settings/users/", data, follow=True)

    def test_create_a_user(self):
        r = self.post(action="create", username="luca", password1="another-pass-42", password2="another-pass-42")
        self.assertContains(r, "User luca created.")
        luca = get_user_model().objects.get(username="luca")
        self.assertTrue(luca.check_password("another-pass-42"))
        self.assertTrue(luca.is_staff)

    def test_create_rejections(self):
        cases = [
            (dict(username="ab", password1=PASSWORD, password2=PASSWORD), "3 to 150"),
            (dict(username="ana", password1="another-pass-42", password2="another-pass-42"), "already exists"),
            (dict(username="Ana", password1="another-pass-42", password2="another-pass-42"), "already exists"),
            (dict(username="luca", password1="another-pass-42", password2="x"), "do not match"),
            (dict(username="luca", password1="password123", password2="password123"), "too common"),
            (dict(username="marcorossi", password1="marcorossi1", password2="marcorossi1"), "too similar"),
        ]
        for data, fragment in cases:
            r = self.post(action="create", **data)
            self.assertContains(r, fragment)
        self.assertFalse(get_user_model().objects.filter(username="luca").exists())
        self.assertFalse(get_user_model().objects.filter(username="marcorossi").exists())

    def test_change_own_password_needs_the_current_one(self):
        r = self.post(action="change_own", current="wrong-current-1", password1="new-pass-2024x",
                      password2="new-pass-2024x")
        self.assertContains(r, "current password is wrong")
        self.ana.refresh_from_db()
        self.assertTrue(self.ana.check_password(PASSWORD))

    def test_change_own_password_keeps_this_session_only(self):
        other = Client()
        other.login(username="ana", password=PASSWORD)
        r = self.post(action="change_own", current=PASSWORD, password1="new-pass-2024x",
                      password2="new-pass-2024x")
        self.assertContains(r, "Other sessions were closed")
        self.assertEqual(self.client.get("/settings/users/").status_code, 200)
        self.assertEqual(other.get("/settings/users/").status_code, 302, "l'altra sessione deve chiudersi")

    def test_change_own_password_is_validated(self):
        r = self.post(action="change_own", current=PASSWORD, password1="password123", password2="password123")
        self.assertContains(r, "too common")

    def test_admin_resets_another_password_and_its_sessions_end(self):
        luca = make_user("luca", "old-luca-pass-1")
        theirs = Client()
        theirs.login(username="luca", password="old-luca-pass-1")
        r = self.post(action="reset", user_id=luca.pk, password1="fresh-luca-pass-9",
                      password2="fresh-luca-pass-9")
        self.assertContains(r, "Password of luca changed")
        self.assertEqual(theirs.get("/settings/users/").status_code, 302)
        self.assertTrue(Client().login(username="luca", password="fresh-luca-pass-9"))

    def test_delete_another_user(self):
        luca = make_user("luca")
        r = self.post(action="delete", user_id=luca.pk)
        self.assertContains(r, "User luca deleted.")
        self.assertFalse(get_user_model().objects.filter(username="luca").exists())

    def test_cannot_delete_yourself(self):
        r = self.post(action="delete", user_id=self.ana.pk)
        self.assertContains(r, "You cannot delete the account you are using")
        self.assertTrue(get_user_model().objects.filter(pk=self.ana.pk).exists())

    def test_cannot_delete_the_last_user(self):
        # un altro account, cancellato dall'interno: ana resta l'unica
        luca = make_user("luca")
        self.post(action="delete", user_id=luca.pk)
        self.assertEqual(get_user_model().objects.count(), 1)
        r = self.post(action="delete", user_id=self.ana.pk)
        self.assertContains(r, "You cannot delete the account you are using")

    def test_unknown_action_and_user(self):
        self.assertContains(self.post(action="drop-everything"), "Unknown action")
        self.assertContains(self.post(action="delete", user_id="999"), "Unknown user")

    def test_the_page_lists_users_and_marks_you(self):
        make_user("luca")
        page = self.client.get("/settings/users/").content.decode()
        self.assertIn("luca", page)
        self.assertIn('badge bg-primary">you', page)

    def test_the_settings_menu_links_to_the_page_and_logout_is_in_the_navbar(self):
        page = self.client.get("/settings/").content.decode()
        self.assertIn('href="/settings/users/"', page)
        self.assertIn('action="/logout/"', page)
        self.assertIn("ana", page)


class DefaultAccountTests(StateDirMixin, TestCase):
    """L'utente admin / hereditary esiste dopo le migrazioni e mostra un avviso finche' e' in uso."""

    def test_the_migration_creates_the_default_admin(self):
        admin = get_user_model().objects.get(username="admin")
        self.assertTrue(admin.check_password("hereditary"))
        self.assertTrue(admin.is_staff)

    def test_signing_in_with_the_default_password_shows_the_banner(self):
        c = Client()
        r = c.post("/login/", {"username": "admin", "password": "hereditary"})
        self.assertEqual(r.status_code, 302, "il login deve passare dalla vista di login")
        page = c.get("/settings/users/").content.decode()
        self.assertIn("signed in with the default password", page)
        self.assertIn('href="/settings/users/"', page)

    def test_the_banner_goes_away_once_the_password_is_changed(self):
        c = Client()
        c.login(username="admin", password="hereditary")
        c.post("/settings/users/", {"action": "change_own", "current": "hereditary",
                                    "password1": "a-new-secret-41", "password2": "a-new-secret-41"})
        page = c.get("/settings/users/").content.decode()
        self.assertNotIn("signed in with the default password", page)
        again = Client()
        again.login(username="admin", password="a-new-secret-41")
        self.assertNotIn("signed in with the default password", again.get("/settings/users/").content.decode())

    def test_another_user_gets_no_banner(self):
        make_user("ana")
        c = Client()
        c.login(username="ana", password=PASSWORD)
        self.assertNotIn("signed in with the default password", c.get("/settings/users/").content.decode())

    def test_a_reset_by_another_user_closes_the_admin_sessions(self):
        admin_session = Client()
        admin_session.login(username="admin", password="hereditary")
        boss = make_user("boss")
        c = Client()
        c.login(username="boss", password=PASSWORD)
        admin = get_user_model().objects.get(username="admin")
        c.post("/settings/users/", {"action": "reset", "user_id": admin.pk,
                                    "password1": "reset-by-boss-77", "password2": "reset-by-boss-77"})
        self.assertEqual(admin_session.get("/settings/users/")["Location"].split("?")[0], "/login/")
        self.assertTrue(boss.pk)

    def test_the_migration_does_not_touch_an_installation_with_users(self):
        from importlib import import_module
        migration = import_module("myapp.migrations.0002_default_admin")
        get_user_model().objects.all().delete()
        make_user("ana")
        from django.apps import apps
        migration.create_default_admin(apps, None)
        self.assertFalse(get_user_model().objects.filter(username="admin").exists())



class PublicPrefixTests(StateDirMixin, TestCase):
    """Il prefisso pubblico si riconosce a ogni richiesta, senza configurazione (myproject/prefix.py)."""

    def setUp(self):
        super().setUp()
        make_user()
        from django.urls import set_script_prefix
        self.addCleanup(set_script_prefix, "/")

    def call(self, path, headers=None):
        """(stato, Location, base URL degli statici) come li vede una risposta WSGI."""
        from django.core.handlers.wsgi import WSGIHandler
        from django.contrib.staticfiles.storage import staticfiles_storage
        from django.test import RequestFactory
        from django.urls import set_script_prefix
        from myproject.prefix import PrefixApp
        seen = {}
        def start_response(status, response_headers, exc_info=None):
            seen["status"], seen["headers"] = status, dict(response_headers)
        # il prefisso degli statici si legge da dentro la richiesta, dopo che Django ha impostato
        # lo script prefix: lo registra un middleware di prova, che gira per ogni pagina
        from django.test import override_settings as _override
        import myapp.tests.test_accounts as me
        me.SEEN_STATIC = seen
        middleware = list(__import__("django.conf", fromlist=["settings"]).settings.MIDDLEWARE)
        middleware.insert(0, "myapp.tests.test_accounts.StaticSpyMiddleware")
        handler_cm = _override(MIDDLEWARE=middleware)
        handler_cm.enable()
        self.addCleanup(handler_cm.disable)
        app = WSGIHandler()
        try:
            env = RequestFactory().get(path, **(headers or {})).environ
            b"".join(PrefixApp(app)(env, start_response))
        finally:
            set_script_prefix("/")
        return seen["status"], seen["headers"].get("Location"), seen["static"]

    def test_the_prefix_is_read_from_the_path(self):
        from myproject.prefix import detect
        cases = {
            "/login/": "", "/": "", "/settings/users/": "",
            "/api1/login/": "/api1", "/api1": "/api1", "/api1/": "/api1",
            "/abcd/settings/": "/abcd", "/tdn-node/endpoint/settings/": "/tdn-node/endpoint",
            "/api1/static/myapp/logo.png": "/api1", "/static/myapp/logo.png": "",
            "/ontop/status/": "", "/api1/ontop/status/": "/api1",
            # un segmento sconosciuto e' indistinguibile da un prefisso: la home sotto quel prefisso
            "/nonexistent/": "/nonexistent", "/nonexistent/settings/": "/nonexistent",
        }
        for path, expected in cases.items():
            self.assertEqual(detect({"PATH_INFO": path}), expected, path)

    def test_the_header_wins_and_is_validated(self):
        from myproject.prefix import detect
        self.assertEqual(detect({"PATH_INFO": "/login/", "HTTP_X_FORWARDED_PREFIX": "/zz"}), "/zz")
        self.assertEqual(detect({"PATH_INFO": "/login/", "HTTP_X_FORWARDED_PREFIX": "/zz/"}), "/zz")
        self.assertEqual(detect({"PATH_INFO": "/login/", "HTTP_X_FORWARDED_PREFIX": "zz"}), "")
        self.assertEqual(detect({"PATH_INFO": "/login/", "HTTP_X_FORWARDED_PREFIX": "/a/../b"}), "")

    def test_redirects_keep_the_prefix_whatever_it_is(self):
        for path, expected in (
            ("/api1/settings/", "/api1/login/?next=/api1/settings/"),
            ("/abcd/settings/", "/abcd/login/?next=/abcd/settings/"),
            ("/tdn-node/endpoint/settings/", "/tdn-node/endpoint/login/?next=/tdn-node/endpoint/settings/"),
        ):
            status, location, _ = self.call(path)
            self.assertTrue(status.startswith("302"), (path, status))
            self.assertEqual(location, expected, path)

    def test_the_proxy_that_strips_the_prefix_works_too(self):
        status, location, _ = self.call("/settings/")
        self.assertEqual(location, "/login/?next=/settings/")

    def test_a_forwarded_prefix_is_used_when_the_path_is_already_stripped(self):
        status, location, static = self.call("/settings/", {"HTTP_X_FORWARDED_PREFIX": "/zz"})
        self.assertEqual(location, "/zz/login/?next=/zz/settings/")
        self.assertEqual(static, "/zz/static/")

    def test_static_files_follow_the_prefix(self):
        _, _, static = self.call("/api1/login/")
        self.assertEqual(static, "/api1/static/")
        _, _, static = self.call("/login/")
        self.assertEqual(static, "/static/")

    def test_public_paths_are_recognised_after_the_prefix(self):
        status, _, _ = self.call("/api1/ontop/status/")
        self.assertTrue(status.startswith("200"), status)
        status, _, _ = self.call("/api1/login/")
        self.assertTrue(status.startswith("200"), status)

    def test_health_check_without_prefix_still_works(self):
        status, _, _ = self.call("/ontop/status/")
        self.assertTrue(status.startswith("200"), status)

    def test_sign_in_returns_to_the_prefixed_page(self):
        r = Client().post("/login/", {"username": "ana", "password": PASSWORD, "next": "/api1/settings/"})
        self.assertEqual(r["Location"], "/api1/settings/")


SEEN_STATIC = {}


class StaticSpyMiddleware:
    """Solo per i test: registra la base URL degli statici vista durante la richiesta."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.contrib.staticfiles.storage import staticfiles_storage
        SEEN_STATIC["static"] = staticfiles_storage.base_url
        return self.get_response(request)


class StrippingProxyTests(StateDirMixin, TestCase):
    """Apache con ``ProxyPass /api1 http://127.0.0.1:8071/``: il prefisso arriva tolto, senza header.

    L'applicazione non puo' conoscerlo: i redirect sono assoluti verso l'indirizzo del backend
    (ProxyPassReverse li riscrive) e gli URL nelle pagine sono relativi alla pagina.
    """

    PROXY = {"HTTP_X_FORWARDED_HOST": "tdn.dei.unipd.it", "HTTP_HOST": "127.0.0.1:8071"}

    def setUp(self):
        super().setUp()
        make_user()
        from django.urls import set_script_prefix
        self.addCleanup(set_script_prefix, "/")

    def get(self, path, **extra):
        from django.test import Client as C
        return C(**self.PROXY).get(path, **extra)

    def test_the_double_slash_apache_sends_is_not_a_prefix(self):
        from myproject.prefix import detect, collapse_slashes
        self.assertEqual(collapse_slashes("//data/"), "/data/")
        for path in ("//data/", "//", "///settings/users/", "/data/"):
            self.assertEqual(detect({"PATH_INFO": path}), "", path)

    def test_redirects_are_absolute_on_the_backend_with_a_double_slash(self):
        """Apache sostituisce 'http://127.0.0.1:8071/' con '/api1': serve uno slash in piu' per non perdere quello del percorso."""
        r = self.get("/settings/")
        self.assertEqual(r["Location"], "http://127.0.0.1:8071//login/?next=/settings/")

    def test_redirect_from_the_root_page(self):
        r = self.get("/")
        self.assertEqual(r["Location"], "http://127.0.0.1:8071//login/?next=/")

    def test_a_relative_redirect_is_resolved_against_the_request_path(self):
        from myapp.proxy import absolute_backend_url
        from django.test import RequestFactory
        req = RequestFactory().get("/settings/users/", **self.PROXY)
        self.assertEqual(absolute_backend_url(req, "../../data/"), "http://127.0.0.1:8071//data/")
        self.assertEqual(absolute_backend_url(req, "/data/"), "http://127.0.0.1:8071//data/")
        self.assertEqual(absolute_backend_url(req, "https://other.example/x"), "https://other.example/x")
        self.assertEqual(absolute_backend_url(req, "//other.example/x"), "//other.example/x")

    def test_relative_prefix_follows_the_depth_of_the_page(self):
        from myproject.prefix import relative_prefix
        for path, expected in (("/", "./"), ("/login/", "../"), ("/data/", "../"),
                               ("/settings/users/", "../../"), ("/map-fields/bootstrap/", "../../")):
            self.assertEqual(relative_prefix(path), expected, path)

    def test_pages_use_urls_relative_to_the_page(self):
        c = logged_in_client()
        c.defaults.update(self.PROXY)
        for path, expected_prefix in (("/data/", "../"), ("/settings/users/", "../../")):
            html = c.get(path).content.decode()
            self.assertIn(f'href="{expected_prefix}static/', html, path)
            self.assertIn(f'href="{expected_prefix}data/"', html, path)
            self.assertNotIn('href="/data/"', html, path)
            self.assertNotIn('src="/static/', html, path)

    def test_without_a_proxy_urls_are_from_the_root(self):
        html = logged_in_client().get("/data/").content.decode()
        self.assertIn('href="/data/"', html)
        self.assertIn('/static/myapp/', html)

    def test_the_root_goes_to_a_page_with_a_trailing_slash(self):
        """Il browser puo' essere su /api1 o /api1/: il backend li vede uguali."""
        c = logged_in_client()
        c.defaults.update(self.PROXY)
        r = c.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r["Location"], "http://127.0.0.1:8071//home/")
        self.assertEqual(c.get("/home/").status_code, 200)

    def test_the_root_is_still_the_home_without_a_proxy(self):
        self.assertEqual(logged_in_client().get("/").status_code, 200)

    def test_csrf_accepts_the_public_origin_behind_a_proxy(self):
        import re
        for origin in ("https://tdn.dei.unipd.it", "http://tdn.dei.unipd.it"):
            c = Client(enforce_csrf_checks=True, **self.PROXY)  # un client nuovo per ogni origine
            page = c.get("/login/").content.decode()
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
            r = c.post("/login/", {"csrfmiddlewaretoken": token, "username": "ana", "password": PASSWORD},
                       HTTP_ORIGIN=origin)
            self.assertEqual(r.status_code, 302, f"Origin {origin} deve essere accettato")

    def test_csrf_still_rejects_other_domains_behind_a_proxy(self):
        c = Client(enforce_csrf_checks=True, **self.PROXY)
        page = c.get("/login/").content.decode()
        import re
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        for origin in ("https://evil.example", "https://tdn.dei.unipd.it.evil.example",
                       "https://evil.example/tdn.dei.unipd.it", "https://tdn.dei.unipd.it:8443",
                       "https://tdn.dei.unipd.it@evil.example", "null", "ftp://tdn.dei.unipd.it",
                       "http://127.0.0.1:9"):
            r = c.post("/login/", {"csrfmiddlewaretoken": token, "username": "ana", "password": PASSWORD},
                       HTTP_ORIGIN=origin)
            self.assertEqual(r.status_code, 403, f"Origin {origin} deve essere rifiutato")

    def test_csrf_is_unchanged_without_a_proxy(self):
        c = Client(enforce_csrf_checks=True)
        page = c.get("/login/").content.decode()
        import re
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
        r = c.post("/login/", {"csrfmiddlewaretoken": token, "username": "ana", "password": PASSWORD},
                   HTTP_ORIGIN="https://tdn.dei.unipd.it")
        self.assertEqual(r.status_code, 403, "senza proxy l'Origin deve coincidere con l'Host")

    def test_instances_on_the_same_domain_do_not_share_cookies(self):
        from django.conf import settings
        self.assertRegex(settings.SESSION_COOKIE_NAME, r"^hdn_session_[0-9a-f]{8}$")
        self.assertRegex(settings.CSRF_COOKIE_NAME, r"^hdn_csrf_[0-9a-f]{8}$")
        self.assertNotEqual(settings.SESSION_COOKIE_NAME, "sessionid")

    def test_the_instance_id_is_stable_and_private(self):
        import os, stat, tempfile
        from importlib import reload
        import myproject.settings as cfg
        with tempfile.TemporaryDirectory() as d:
            old = cfg.HDN_STATE_DIR
            cfg.HDN_STATE_DIR = d
            try:
                first = cfg._instance_id()
                self.assertEqual(cfg._instance_id(), first)
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d, "instance-id")).st_mode), 0o600)
            finally:
                cfg.HDN_STATE_DIR = old
