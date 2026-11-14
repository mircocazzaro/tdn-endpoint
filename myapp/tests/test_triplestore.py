"""GraphDB come sorgente: configurazione, pagina a tre sezioni e dispatch delle query.

Con un triple store attivo la query SPARQL, superato il controllo di privacy,
va a GraphDB e non a Ontop. Qui il server e' simulato (requests); la prova con
un GraphDB reale sta fuori dalla suite.
"""
import os
import stat
from unittest import mock

import requests
from django.test import Client, TestCase, override_settings
from .helpers import logged_in_client

from myapp import catalog
from myapp import datasources as ds
from myapp import ontop_process, triplestore
from myapp.tests.helpers import LevelStores, central_request

from .hdn_helpers import StateDirMixin

URL = "http://graphdb:7200"
GRAPHDB_ENDPOINT = URL + "/repositories/hero"
ONTOP = "http://127.0.0.1:1/sparql"
RESULT = {"head": {"vars": ["nDISEASE"]},
          "results": {"bindings": [{"nDISEASE": {"type": "literal", "value": "7"}}]}}


def reply(payload=None, status=200, text=""):
    r = mock.Mock(status_code=status, text=text or "")
    r.json.return_value = payload if payload is not None else RESULT
    r.raise_for_status.side_effect = (
        None if status < 400 else requests.HTTPError(f"HTTP {status}"))
    return r


def configure(user="", password=""):
    ds.save_remote("graphdb", {"url": URL, "repository": "hero", "user": user, "password": password})


class RegistryTests(StateDirMixin, TestCase):

    def test_three_sections_and_their_sources(self):
        groups = {gid: [k for k, v in ds.KINDS.items() if v.kind in kinds]
                  for gid, (_t, kinds) in ds.GROUPS.items()}
        self.assertEqual(groups["files"], ["csv", "duckdb", "sqlite"])
        self.assertEqual(groups["rdbms"], ["mysql", "postgresql", "oracle", "mssql", "aurora"])
        self.assertEqual(groups["triplestore"], ["graphdb"])
        self.assertEqual([t for t, _ in ds.GROUPS.values()], ["Files", "RDBMS", "Triple store"])

    def test_credentials_are_optional_url_and_repository_are_not(self):
        self.assertEqual(ds.missing("graphdb", {}), ["Server URL", "Repository"])
        configure()
        self.assertTrue(ds.configured("graphdb"))
        self.assertEqual(ds.missing("graphdb", None), [])

    def test_password_is_kept_when_blank_and_never_shown(self):
        configure("ana", "S3CRET")
        ds.save_remote("graphdb", {"url": URL, "repository": "hero", "user": "ana", "password": ""})
        self.assertEqual(ds.stored("graphdb")["password"], "S3CRET")
        pub = ds.public("graphdb")
        self.assertTrue(pub["has_password"])
        self.assertNotIn("S3CRET", str(pub))
        self.assertEqual(stat.S_IMODE(os.stat(ds._registry_path()).st_mode), 0o600)

    def test_a_triple_store_has_no_tables(self):
        configure()
        with self.assertRaises(ds.DataSourceError) as cm:
            ds.list_tables("graphdb")
        self.assertIn("SPARQL", cm.exception.detail)

    def test_active_triplestore_flag(self):
        self.assertFalse(ds.active_is_triplestore())
        configure()
        ds.set_active("graphdb")
        self.assertTrue(ds.active_is_triplestore())


class UrlValidationTests(TestCase):

    def test_urls(self):
        v = triplestore.valid_url
        self.assertTrue(v("http://graphdb:7200"))
        self.assertTrue(v("https://graphdb.example.org"))
        self.assertFalse(v("ftp://x"))
        self.assertFalse(v("graphdb:7200"))
        self.assertFalse(v(""))
        self.assertFalse(v("http://user:pw@host"), "credenziali dentro l'URL")

    def test_credentials_need_https_unless_the_server_is_local(self):
        v = triplestore.valid_url
        self.assertFalse(v("http://graphdb:7200", True))
        self.assertTrue(v("https://graphdb:7200", True))
        self.assertTrue(v("http://localhost:7200", True))
        self.assertTrue(v("http://127.0.0.1:7200", True))

    def test_repository_names(self):
        self.assertTrue(triplestore.valid_repository("hero"))
        self.assertTrue(triplestore.valid_repository("hero-2.v1_x"))
        for bad in ("", "a/b", "a b", "../x", "x?y", "-x"):
            self.assertFalse(triplestore.valid_repository(bad), bad)


class ClientTests(StateDirMixin, TestCase):

    def test_query_goes_to_the_repository_with_json_accept(self):
        configure()
        with mock.patch("requests.post", return_value=reply()) as post:
            data = triplestore.query("SELECT * WHERE {?s ?p ?o}", timeout=5)
        self.assertEqual(data, RESULT)
        self.assertEqual(post.call_args.args[0], GRAPHDB_ENDPOINT)
        self.assertEqual(post.call_args.kwargs["data"], {"query": "SELECT * WHERE {?s ?p ?o}"})
        self.assertEqual(post.call_args.kwargs["headers"]["Accept"], "application/sparql-results+json")
        self.assertEqual(post.call_args.kwargs["timeout"], 5)
        self.assertIsNone(post.call_args.kwargs["auth"])

    def test_basic_auth_only_when_a_user_is_set(self):
        configure("ana", "pw")
        with mock.patch("requests.post", return_value=reply()) as post:
            triplestore.query("ASK {}")
        self.assertEqual(post.call_args.kwargs["auth"], ("ana", "pw"))

    def test_repository_name_is_url_quoted(self):
        ds.save_remote("graphdb", {"url": URL + "/", "repository": "a b", "user": "", "password": ""})
        self.assertEqual(triplestore.endpoint_url(), URL + "/repositories/a%20b")

    def test_unconfigured(self):
        with self.assertRaises(triplestore.TripleStoreError) as cm:
            triplestore.query("ASK {}")
        self.assertEqual(cm.exception.code, "not-configured")

    def test_error_codes(self):
        configure("ana", "S3CRET")
        cases = [(401, "unauthorized"), (403, "unauthorized"), (404, "not-found"), (500, "http-error")]
        for status, code in cases:
            with mock.patch("requests.post", return_value=reply(status=status, text="boom S3CRET")):
                with self.assertRaises(triplestore.TripleStoreError) as cm:
                    triplestore.query("ASK {}")
            self.assertEqual(cm.exception.code, code)
            self.assertNotIn("S3CRET", str(cm.exception))

    def test_timeout_and_unreachable(self):
        configure()
        with mock.patch("requests.post", side_effect=requests.Timeout("slow")):
            with self.assertRaises(triplestore.TripleStoreError) as cm:
                triplestore.query("ASK {}")
        self.assertEqual(cm.exception.code, "timeout")
        with mock.patch("requests.post", side_effect=requests.ConnectionError("no route to S3CRET")):
            with self.assertRaises(triplestore.TripleStoreError) as cm:
                triplestore.query("ASK {}")
        self.assertEqual(cm.exception.code, "unreachable")

    def test_non_json_answer(self):
        configure()
        r = reply()
        r.json.side_effect = ValueError("not json")
        with mock.patch("requests.post", return_value=r):
            with self.assertRaises(triplestore.TripleStoreError) as cm:
                triplestore.query("ASK {}")
        self.assertEqual(cm.exception.code, "bad-response")

    def test_test_reports_the_statement_count(self):
        configure()
        size = mock.Mock(status_code=200, text="1234\n")
        with mock.patch("requests.post", return_value=reply({"boolean": True})), \
                mock.patch("requests.get", return_value=size) as get:
            self.assertEqual(triplestore.test(), 1234)
        self.assertEqual(get.call_args.args[0], GRAPHDB_ENDPOINT + "/size")


class PageTests(StateDirMixin, TestCase):

    def test_three_headed_sections_with_the_right_cards(self):
        page = logged_in_client().get("/data/").content.decode()
        order = [page.index(f'id="section-{s}"') for s in ("files", "rdbms", "triplestore")]
        self.assertEqual(order, sorted(order))
        for title in ("Files", "RDBMS", "Triple store"):
            self.assertIn(f">{title}</h5>", page)

        def section(sid, nxt):
            start = page.index(f'id="section-{sid}"')
            end = page.index(f'id="section-{nxt}"') if nxt else len(page)
            return page[start:end]

        files, rdbms, ts = section("files", "rdbms"), section("rdbms", "triplestore"), section("triplestore", None)
        for label in ("CSV files", "DuckDB file", "SQLite file"):
            self.assertIn(label, files)
        for label in ("MySQL", "PostgreSQL", "Oracle", "MS SQL Server", "Amazon Aurora"):
            self.assertIn(label, rdbms)
        self.assertIn("GraphDB", ts)
        self.assertNotIn("MySQL", ts)
        self.assertNotIn("GraphDB", files + rdbms)
        self.assertIn("myapp/cropped-graphwise_favicon.png", ts)

    def test_graphdb_form_fields_and_optional_credentials(self):
        page = logged_in_client().get("/data/").content.decode()
        for name in ("url", "repository", "user", "password"):
            self.assertIn(f'id="graphdb-{name}"', page)
        self.assertIn("User (optional)", page)

    def test_save_validates_and_stores(self):
        c = logged_in_client()
        r = c.post("/data/", {"kind": "graphdb", "action": "save", "url": URL, "repository": "hero"},
                   follow=True)
        self.assertContains(r, "GraphDB connection saved")
        self.assertTrue(ds.configured("graphdb"))

    def test_save_rejections(self):
        c = logged_in_client()
        cases = [
            ({"url": "", "repository": "hero"}, "missing"),
            ({"url": "nonsense", "repository": "hero"}, "URL must be http"),
            ({"url": URL, "repository": "he ro"}, "repository name"),
            ({"url": URL, "repository": "hero", "user": "ana", "password": "pw"}, "https"),
        ]
        for fields, fragment in cases:
            r = c.post("/data/", dict(fields, kind="graphdb", action="save"), follow=True)
            self.assertContains(r, fragment)
        self.assertFalse(ds.configured("graphdb"))

    def test_save_and_test_reports_the_server(self):
        with mock.patch("requests.post", return_value=reply({"boolean": True})), \
                mock.patch("requests.get", return_value=mock.Mock(status_code=200, text="42")):
            r = logged_in_client().post("/data/", {"kind": "graphdb", "action": "test", "url": URL,
                                         "repository": "hero"}, follow=True)
        self.assertContains(r, "GraphDB reachable: 42 statements")

    def test_failed_test_shows_the_reason(self):
        with mock.patch("requests.post", return_value=reply(status=404)):
            r = logged_in_client().post("/data/", {"kind": "graphdb", "action": "test", "url": URL,
                                         "repository": "nope"}, follow=True)
        self.assertContains(r, "GraphDB test failed")
        self.assertContains(r, "repository name")


class ActivationTests(StateDirMixin, TestCase):

    def setUp(self):
        super().setUp()
        configure()

    def test_activation_stops_ontop(self):
        with mock.patch.object(ontop_process, "is_running", return_value=True), \
                mock.patch.object(ontop_process, "stop") as stop:
            r = logged_in_client().post("/data/", {"kind": "graphdb", "action": "activate"}, follow=True)
        stop.assert_called_once()
        self.assertEqual(ds.active(), "graphdb")
        self.assertContains(r, "Ontop and the mapping are not used")

    def test_an_unconfigured_triplestore_cannot_be_activated(self):
        ds.save_remote("graphdb", {"url": "", "repository": ""})
        logged_in_client().post("/data/", {"kind": "graphdb", "action": "activate"}, follow=True)
        self.assertEqual(ds.active(), "csv")

    def test_galois_blocks_the_activation(self):
        with mock.patch("myapp.galois.store.enabled", return_value=True):
            r = logged_in_client().post("/data/", {"kind": "graphdb", "action": "activate"}, follow=True)
        self.assertContains(r, "Galois mode is on")
        self.assertEqual(ds.active(), "csv")

    def test_a_triplestore_blocks_enabling_galois(self):
        ds.set_active("graphdb")
        from myapp.galois import store
        r = logged_in_client().post("/galois/", {"action": "save", "enabled": "on", "max_iter": "2"}, follow=True)
        self.assertContains(r, "bypasses it")
        self.assertFalse(store.enabled())

    def test_page_text_and_other_pages_with_a_triplestore_active(self):
        ds.set_active("graphdb")
        c = logged_in_client()
        self.assertContains(c.get("/data/"), "go straight to it")
        r = c.get("/map-fields/", follow=True)
        self.assertEqual(r.redirect_chain[-1][0], "/data/")
        self.assertContains(r, "no mapping to edit")
        r = c.get("/map-fields/knowledge-graph/", follow=True)
        self.assertContains(r, "not available")
        r = c.post("/query/", {"sql_query": "SELECT 1"})
        self.assertIn("triple store", r.context["error"])
        r = c.post("/ontop-control/", {"action": "start"}, follow=True)
        self.assertContains(r, "bypass Ontop")

    def test_ontop_refuses_to_start(self):
        ds.set_active("graphdb")
        with mock.patch.object(ontop_process, "is_running", return_value=False):
            with self.assertRaises(ontop_process.OntopNotApplicable):
                ontop_process.start()

    def test_switching_back_to_a_database_works(self):
        ds.set_active("graphdb")
        ds.set_active("csv")
        self.assertFalse(ds.active_is_triplestore())


class DispatchTests(StateDirMixin, TestCase):
    """Dopo il controllo di privacy la query va a GraphDB, non a Ontop."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.levels = LevelStores()

    @classmethod
    def tearDownClass(cls):
        cls.levels.cleanup()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        configure()
        ds.set_active("graphdb")
        self.client = logged_in_client()
        self.fields = central_request("q02_L1", disease="NCIT:C34373")

    def post(self, level, fields=None, **extra):
        with override_settings(LEVEL_DB=self.levels[level], ONTOP_SPARQL_ENDPOINT=ONTOP):
            return self.client.post("/sparql-protected/", dict(fields or self.fields, **extra))

    def urls(self, post):
        return [c.args[0] for c in post.call_args_list]

    def test_an_allowed_query_goes_to_graphdb_and_never_to_ontop(self):
        with mock.patch("requests.post", return_value=reply()) as post:
            resp = self.post(1)
        self.assertEqual(self.urls(post), [GRAPHDB_ENDPOINT])
        self.assertEqual(resp.json(), RESULT)

    def test_graphdb_receives_the_query_rebuilt_from_the_catalog(self):
        with mock.patch("requests.post", return_value=reply()) as post:
            self.post(1)
        sent = post.call_args.kwargs["data"]["query"]
        self.assertEqual(sent, catalog.match_query(self.fields["query"]).query)
        self.assertIn("COUNT", sent)

    def test_a_refused_level_never_reaches_graphdb(self):
        with mock.patch("requests.post", return_value=reply()) as post:
            resp = self.post(0)
        post.assert_not_called()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"]["bindings"], [])

    def test_an_unknown_query_never_reaches_graphdb(self):
        fields = {"query": catalog.PROLOGUE + "SELECT * WHERE { ?s ?p ?o }"}
        with mock.patch("requests.post", return_value=reply()) as post:
            self.post(6, fields)
        post.assert_not_called()

    def test_graphdb_failures_look_like_no_data(self):
        refused = logged_in_client()
        with mock.patch("requests.post", return_value=reply()):
            refused_resp = self.post(0)
        for failure in (reply(status=500), requests.ConnectionError("down"), requests.Timeout("slow")):
            kwargs = ({"side_effect": failure} if isinstance(failure, Exception)
                      else {"return_value": failure})
            with mock.patch("requests.post", **kwargs):
                resp = self.post(1)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.content, refused_resp.content,
                             "un errore del backend si distingue da un rifiuto")

    def test_the_audit_log_names_the_backend(self):
        with mock.patch("requests.post", return_value=reply()), self.assertLogs("hdn.audit", "INFO") as logs:
            self.post(1)
        self.assertTrue(any("contributed" in m and "backend=graphdb" in m for m in logs.output), logs.output)
        with mock.patch("requests.post", side_effect=requests.ConnectionError("down")), \
                self.assertLogs("hdn.audit", "INFO") as logs:
            self.post(1)
        self.assertTrue(any("backend-error" in m and "backend=graphdb" in m for m in logs.output), logs.output)

    def test_galois_with_a_triplestore_fails_closed(self):
        with mock.patch("myapp.galois.store.enabled", return_value=True), \
                mock.patch("requests.post", return_value=reply()) as post, \
                self.assertLogs("hdn.audit", "INFO") as logs:
            resp = self.post(1)
        post.assert_not_called()
        self.assertEqual(resp.json()["results"]["bindings"], [])
        self.assertTrue(any("galois-with-triplestore" in m for m in logs.output))

    def test_analytics_also_goes_to_graphdb(self):
        with mock.patch("requests.post", return_value=reply({"results": {"bindings": []}})) as post:
            resp = self.post(1, analytics_key="klDiv")
        self.assertEqual(self.urls(post), [GRAPHDB_ENDPOINT])
        self.assertIsNone(resp.json()["kl_divergence"])

    def test_without_a_triplestore_the_query_still_goes_to_ontop(self):
        ds.set_active("csv")
        with mock.patch("requests.post", return_value=reply()) as post:
            self.post(1)
        self.assertEqual(self.urls(post), [ONTOP])

    def test_local_sparql_page_talks_to_graphdb(self):
        with mock.patch("requests.post", return_value=reply()) as post:
            r = self.client.post("/sparql/", {"sparql_query": "SELECT ?n WHERE {}"})
        self.assertEqual(self.urls(post), [GRAPHDB_ENDPOINT])
        self.assertEqual(r.context["sparql_results"]["vars"], ["nDISEASE"])
        self.assertContains(r, "Run a SPARQL Query on GraphDB")

    def test_local_sparql_page_reports_graphdb_errors(self):
        with mock.patch("requests.post", return_value=reply(status=401)):
            r = self.client.post("/sparql/", {"sparql_query": "ASK {}"})
        self.assertIn("password", r.context["sparql_error"])

    def test_local_sparql_page_without_a_triplestore_says_ontop(self):
        ds.set_active("csv")
        r = self.client.get("/sparql/")
        self.assertContains(r, "Run a SPARQL Query on Ontop VKG")
