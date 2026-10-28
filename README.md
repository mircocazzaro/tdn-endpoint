# HDN Central

> **Research use only. Not a medical device.**
> This software was developed in the HEREDITARY project as a research prototype. It has not been assessed or certified under the EU Medical Device Regulation (EU) 2017/745. It must not be used to diagnose, treat, monitor or make any other decision about individual patients.

## 1. Overview

HDN Central is a Django web application that dispatches catalog SPARQL queries to registered HDN endpoints (`tdn-endpoint`) and combines their answers. It manages endpoint enrollment, distributes query catalogs and ontologies, and supports visual query composition and rewriting. It also includes exploratory analytics and an interactive decision-tree regression feature.

| Field | Value |
|---|---|
| Name | HDN Central |
| Version | Repository snapshot `90d781a4b3a0a53a72d0680b103bcb8a2917b118`; no release tag is present in the inspected checkout |
| Type | Software / web application; includes runtime model training, rather than a separately packaged pretrained model |
| Owner partner and contact | Author and maintainer: Mirco Cazzaro, mirco.cazzaro.1999@gmail.com. Affiliation: University of Padua (UNIPD). Developed within the EU-funded HEREDITARY project. This attribution does not determine copyright ownership. |
| License | Apache-2.0; see LICENSE. Bundled third-party components retain their own licenses. |
| Repository and DOI | https://github.com/mircocazzaro/central-tdn; no software DOI is documented in the repository |
| Release date | 28 October 2026, the date of the documented commit; this identifies the inspected snapshot rather than a separately tagged release |
| Related task or deliverable | HEREDITARY Data Network software; this README follows the documentation template requested for D9.1. A development task or deliverable identifier for Central is not specified in the repository. |
| How to cite | Cazzaro, M. (2026). HDN Central [Software, commit 90d781a4b3a0a53a72d0680b103bcb8a2917b118]. https://github.com/mircocazzaro/central-tdn |

## 2. Intended use

**Primary intended use.** Research on distributed querying of knowledge graphs through a shared query catalog and ontology, including query rewriting under endpoint-specific disclosure levels. The analytics interface supports exploratory summaries and decision-tree regression on query results; these are research functions, not validated clinical tools.

**Intended users.** Researchers, knowledge engineers and developers working on semantic data integration, distributed query processing and biomedical research infrastructure.

**Out-of-scope uses.** This release must not be used for:

- clinical diagnosis, prognosis, monitoring or treatment of individual patients;
- decisions about individuals, such as insurance, employment or access to care;
- re-identification of individuals or unauthorized linkage with other data sources;
- clinical or population-level conclusions beyond the scope of an independently approved and validated research study;
- treating LLM-derived endpoint content as verified clinical evidence.

Do not enter personal or patient data into the research web application. Endpoint connectivity does not itself authorize access to institutional data. Query responses can contain detailed records, depending on endpoint configuration; keeping the source database at its institution does not mean Central cannot receive sensitive information.

## 3. Data provenance

Central is software rather than a fixed research dataset. Data used during operation come from configured endpoints, and their provenance, access conditions and study populations must be established for each deployment.

| Dataset | Source partner | Type (public, synthetic, pseudonymized, anonymized) | Population (size, age, sex, country, condition) | Ethics approval or access conditions |
|---|---|---|---|---|
| Query answers received during operation | Registered institutions or Galois endpoints | Depends on endpoint configuration; no single classification is established by this release | Depends on the queried endpoint; no study cohort is documented in the repository | Institutional authorization and source-specific conditions apply; no cohort approval is documented here |
| Runtime regression training data | Answers returned by registered endpoints for the selected catalog query | Depends on the source answers; not established as anonymized | Rows with event type, sex, endpoint and age at onset; no fixed population size, country or cohort is documented | Same access conditions as the source query results; no separate training dataset approval is documented |
| Versioned application databases: `db.sqlite3` and `db_old.sqlite3` | Existing Central application state; generating institution not established | Account, session and endpoint configuration data; not established as synthetic or anonymized | Not a clinical cohort. Each database contains one account; they also contain session and endpoint records. | Included in the inspected public checkout; no explicit data-release conditions are documented |
| `data/allowed_queries.duckdb`, endpoint logos and `nohup.out` | Repository assets and application state | Query-related database asset, images and application log; no clinical dataset provenance is documented | Not documented as a patient cohort | No separate release-level provenance statement is provided |

The Docker build excludes the bundled SQLite databases, `data/`, `media/` and `nohup.out` through `.dockerignore`. This exclusion does not remove those files from the Git repository or its history.

Galois endpoints supply LLM-derived content intended to represent public knowledge. Central does not independently establish the provenance or factual accuracy of that content.

## 4. Method

- **Algorithm or architecture.** Django mediator over SPARQL endpoints. Exact catalog-template instances are dispatched directly. Other supported queries are rewritten using MiniCon over row-returning catalog queries, including derived sub-patterns. Central evaluates joins, filters and aggregates using an in-memory DuckDB database. Joins remain within each endpoint.
- **Training setting.** Query rewriting itself does not train a model. The optional analytics feature trains a scikit-learn `DecisionTreeRegressor` centrally on returned query rows. It one-hot encodes event type, sex and endpoint, and predicts age at onset. It uses a 70%/30% train/test split, with default split seed 42 unless a different `random_state` is supplied. Trained models are stored in process memory, not released as pretrained model files.
- **Privacy techniques.** Catalog access uses Django login; endpoint management uses a separate session-based password check. Enrollment, catalog and ontology protocols use Ed25519 signatures and replay checks. Query dispatch to `/sparql-protected/` is an ordinary HTTP(S) POST without those signatures. Disclosure-level enforcement is delegated to endpoints. No differential privacy or secure aggregation mechanism is implemented in the inspected query and training paths.

For rewritten aggregate queries, Central reports per-endpoint and combined results. Identical entity IRIs on different endpoints are treated as different entities for global distinct counts. Whole-template matches return the endpoint answers directly and do not generally compute a combined aggregate.

## 5. Performance

- **Evaluation data.** The repository contains synthetic RDF graphs and simulated endpoint responses in `catalogapp/tests/test_rewriting.py`, plus Django interface tests in `catalogapp/tests/test_compose.py`. No external clinical validation dataset or fixed clinical train/test cohort is documented.
- **Metrics and results.** Tests cover rewriting equivalence on synthetic graphs, multiplicity, endpoint-local entity identity, plan-level selection, parameter restrictions, unsupported queries, derived catalog patterns and compose-page behavior. The runtime regression interface computes mean squared error and R-squared on its held-out test split. No fixed numerical regression results, confidence intervals, clinical accuracy results or throughput benchmarks are documented. The test suite was not executed during preparation of this README; its presence is not a claim that all tests passed.
- **Results by subgroup.** No performance results by age, sex, institution or other subgroup are documented. Including sex or endpoint as a regression feature does not constitute subgroup validation.

Run the repository's functional tests with:

```bash
python manage.py test catalogapp
```

The compose-page JavaScript syntax test also requires Node.js.

## 6. Known limitations and risks

- **Generalizability.** Query answers depend on endpoint availability, catalog and ontology versions, mappings, source data and disclosure levels. Regression performance across institutions and patient populations has not been established.
- **Failure modes.** Unsupported SPARQL constructs are refused. An endpoint may return an empty answer when a query exceeds its disclosure level, making restricted results indistinguishable from absent data. Endpoints missing a required response are excluded from a rewriting result. Rewriting processes at most 50,000 returned rows per call per endpoint, which can truncate answers and affect aggregates. Joins across endpoints are not supported. Runtime models are lost when the application process restarts.
- **Privacy risks.** Returned rows, aggregates and trained trees may reveal information about source data. Local source storage and query-catalog restrictions are not anonymization guarantees. The admission module currently admits every rewriting plan. The training code prints a dataframe preview and its SPARQL query to application output, creating a potential log disclosure path. Runtime models are kept in a process-wide dictionary without a documented per-user ownership check. No membership-inference, model-inversion or formal disclosure evaluation is documented.

## 7. Responsible use and reporting

- **Terms of use.** The project code is distributed under the Apache License, Version 2.0; see LICENSE. Research is the intended use described in Section 2. The research and medical-device notices describe intended use and validation status; they do not amend Apache-2.0 or add field-of-use restrictions to it. Use only authorized endpoints and data; configure deployment secrets, access restrictions and transport protection before operation.
- **Web application notice.** The HEREDITARY template also requires the following notice in the user interface: “Research prototype of the HEREDITARY project. Not a medical device and not for clinical use. Do not enter personal or patient data.” This notice was not found in the inspected HTML templates; the README disclaimer alone does not implement it in the application.
- **Contact for misuse or vulnerability reports.** Mirco Cazzaro: mirco.cazzaro.1999@gmail.com. Report sensitive findings privately; do not post patient records, credentials, session values or private keys in public issues.

## 8. Acknowledgement

Funded by the European Union under grant agreement No 101137074 (HEREDITARY). Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or the European Health and Digital Executive Agency (HaDEA). Neither the European Union nor the granting authority can be held responsible for them.

## Installation

Python 3.12 is used by the supplied Docker image. Install the dependencies pinned in `requirements.txt`.

```bash
git clone https://github.com/mircocazzaro/central-tdn.git
cd central-tdn
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser      # account used to log in to the catalog
python manage.py runserver 0.0.0.0:8000
```

Open `http://<host>:8000/` and log in with the account you just created.

## Installation with Docker

```bash
git clone https://github.com/mircocazzaro/central-tdn.git
cd central-tdn
CENTRAL_ADMIN_PASSWORD='choose-one' docker compose up -d --build
```

Central listens on port 8000. The login account `admin` (or
`CENTRAL_ADMIN_USER`) is created on first start. Database and logos are kept
in the `central-state` volume.

## Endpoints joining the network

Endpoints apply from their **HDN Network** page by entering this Central's
URL. Applications appear under **Endpoints** in the navbar (manager password:
`ENDPOINT_MANAGER_PASSWORD` in `centralproject/settings.py`), with the
endpoint's key fingerprint. When you approve an application, Central notifies
the endpoint. The endpoint joins only if it answers, signed with the key it
applied with, at the URL it declared. A new member immediately receives the
latest published catalog and ontology.

Endpoints can still be added by hand (**+ Add**, base URL such as
`http://tdn.example.org/api0`). They receive queries, but no catalog or
ontology updates, because Central has no key for them. Central posts queries
to `<base URL>/sparql-protected/`. The logo is optional.

## Distributing the query catalog and the ontology

From the same page:

- **Publish query catalog** sends the catalog in `catalogapp/queries.py` to
  every enrolled endpoint. The version number increases only when the content
  changes. Templates whose parameters have no grammar on the endpoints (today
  `q05_L2`, `starting_date`) are left out, and a message says so.
  `q15_L0`–`q18_L4` (clinical trials, drugs by ATC group) target endpoints in
  Galois mode, whose tables hold public knowledge retrieved from an LLM instead
  of patient data.
- **Publish ontology** needs two files: the ontology (`.ttl`) and the mapping
  template written for it (`.obda`). The template holds the blocks that
  *Map Data to HERO* offers on each endpoint. The version increases when
  either file changes. Each endpoint installs both right away. It also removes
  the site mappings that use terms the new ontology no longer declares. The
  page reports, for each endpoint, how many mappings were removed and kept, or
  why the update was refused: invalid Turtle, `owl:imports` not available
  offline, or a template using terms the ontology does not declare.

The table shows the catalog and ontology version installed on each endpoint.
Enrollment, catalog and ontology protocol messages are signed with Ed25519.
SPARQL query dispatch is not signed. This Central's key is created on
first use in `hdn/identity.pem` (or `$HDN_STATE_DIR/hdn/` in Docker). Its
fingerprint is shown on the Endpoints page. Keep the key: if it changes,
enrolled endpoints refuse this Central. `HDN_CENTRAL_NAME` sets the name shown
to endpoints.

For query dispatch, Central uses a 240 s request timeout per endpoint
(`HDN_ENDPOINT_TIMEOUT`), with up to 16 endpoint workers. Management protocol
requests use a separate 30 s timeout. Galois endpoints first refill their tables
from an LLM and can take that long.

## Composing your own query (query rewriting)

**Compose a query** in the navbar lets users build a query visually or write
it in SPARQL. Endpoints still execute only catalog queries: Central looks for
an equivalent rewriting of the user query in terms of catalog queries, runs
those, and computes joins, filters and aggregates itself.

- **Derived queries.** At every catalog publication, Central factorizes the
  hand-written templates into their sub-patterns. A sub-pattern has at least
  two triples, is connected and contains a typing triple. Each one becomes a
  derived catalog query with a `d_...` key: published to the endpoints like
  the others, but not listed in the legacy Catalog page.
  - A derived query inherits the highest level among the templates it comes
    from.
  - Adding a template adds its sub-patterns and never lowers an existing
    level. Unchanged sub-patterns keep their key.
- **Rewriting.**
  - A query that is an instance of a template runs that template as it is.
  - Otherwise Central applies MiniCon over non-aggregate, row-returning
    catalog queries. A template parameter must be bound to a constant of the
    user query that its grammar accepts.
  - The plan with the lowest level is chosen. Its level is the maximum
    disclosure level among the catalog queries it calls.
  - Results are joined per endpoint, never across endpoints. Rewritten
    aggregates are computed per endpoint and over all endpoints. Identical
    entity IRIs on different endpoints remain distinct. Whole-template
    matches return endpoint answers directly.
  - The plan, with the SPARQL sent to the endpoints, is shown to the user.
- **Admission.** `catalogapp/rewriting/admission.py` decides whether a plan
  may run. Today it admits every plan, and endpoints enforce their own level
  on each catalog query. This module is the intended integration point for a
  new disclosure formalism.
- **Fragment.** Supported: basic graph patterns, FILTER comparisons with
  `&&`, `||`, `!`, ASK, COUNT/SUM/AVG/MIN/MAX, GROUP BY, ORDER BY, LIMIT and
  OFFSET. OPTIONAL, UNION, BIND, sub-queries and variable predicates are
  refused.
- **Known limit.** An endpoint answers empty when a query is above its level,
  so a plan can return less than it should. The page says so. Rewriting also
  processes only the first 50,000 rows per catalog call per endpoint.

## Layout

```text
centralproject/   settings, root URLs
catalogapp/
  queries.py      query catalog (published to enrolled endpoints)
  forms.py        parameter forms and allowed values
  views.py        catalog, dispatch, analytics, endpoint manager
  rewriting/      query rewriting over the catalog (lattice, MiniCon, mediator)
  compose_views.py  "Compose a query" page and API
  tests/          python manage.py test catalogapp
  templates/      UI
```