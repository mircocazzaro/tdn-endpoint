"""Login sull'interfaccia di amministrazione, primo avvio e utenti locali."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from myapp import accounts
from myapp.middleware import is_public

from .hdn_helpers import StateDirMixin

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
        self.assertNotIn("sessionid", r.cookies)

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

