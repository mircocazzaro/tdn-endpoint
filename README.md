# HDN Endpoint

> **Research use only. Not a medical device.**
> This software was developed in the HEREDITARY project as a research prototype. It has not been assessed or certified under the EU Medical Device Regulation (EU) 2017/745. It must not be used to diagnose, treat, monitor or make any other decision about individual patients.

## 1. Overview

HDN Endpoint is the local node of the Hereditary Data Network (HDN), developed within HEREDITARY deliverable D3.2. Each participating institution installs an endpoint alongside its data. The endpoint loads data into a local DuckDB database, maps them to the HERO ontology through Ontop and OBDA mappings, and answers catalog queries within the institution's selected disclosure level.

HDN Central receives permitted query answers rather than direct access to the source database. Those answers can still contain detailed or sensitive data. Optional features provide LLM-assisted mapping and a Galois mode for retrieving public-knowledge content through Azure OpenAI.

| Field | Value |
|---|---|
| Name | HDN Endpoint |
| Version | Repository snapshot `baefad7529c2694f78f2ed6a7a05c6331fc9d178`; no release tag is present in the inspected checkout |
| Type | Software / web application; invokes external LLMs but does not release a pretrained model |
| Owner partner and contact | Author and maintainer: Mirco Cazzaro, mirco.cazzaro@phd.unipd.it. Affiliation: University of Padua (UNIPD). Developed within the EU-funded HEREDITARY project. This attribution does not determine copyright ownership. |
| License | Apache-2.0; see LICENSE. Bundled third-party components retain their own licenses. |
| Repository and DOI | https://github.com/mircocazzaro/tdn-endpoint; no software DOI is documented in the repository |
| Release date | 8 October 2026, the date of the documented commit; this identifies the snapshot rather than a separately tagged release |
| Related task or deliverable | HEREDITARY D3.2; documentation prepared using the template requested for D9.1 |
| How to cite | Cazzaro, M. (2026). HDN Endpoint [Software, commit baefad7529c2694f78f2ed6a7a05c6331fc9d178]. https://github.com/mircocazzaro/tdn-endpoint |

## 2. Intended use

**Primary intended use.** Research on distributed querying and ontology-based integration of institutional data, with catalog restrictions and institution-selected disclosure levels. Optional features support research on LLM-assisted mapping and LLM-derived public knowledge.

**Intended users.** Institutional research data administrators, knowledge engineers, biomedical researchers and developers operating an authorized research environment.

**Out-of-scope uses.** This release must not be used for:

- clinical diagnosis, prognosis, monitoring or treatment of individual patients;
- decisions about individuals, such as insurance, employment or access to care;
- re-identification or unauthorized linkage with other data sources;
- populations or data types outside a documented and authorized research deployment;
- exposure to an untrusted network under the current security configuration;
- treating LLM output as verified clinical evidence or actual patient records.

The software supports local clinical-data ingestion, but this capability does not authorize processing or disclosure of patient data. Any such deployment requires its own documented data governance, access conditions and institutional approvals. The public research demonstration must not accept personal or patient data.

## 3. Data provenance

The release is software, not a fixed clinical dataset. The default mapping template expects patient general data, ALSFRS-R visits and spirometry visits. These schemas do not establish that patient records are distributed with the software.

| Dataset | Source partner | Type (public, synthetic, pseudonymized, anonymized) | Population (size, age, sex, country, condition) | Ethics approval or access conditions |
|---|---|---|---|---|
| Institutional data uploaded during operation | Deploying institution | Determined by the institution; not established as anonymized by this software | Deployment-specific; default schemas cover patient general data, ALSFRS-R and spirometry visits | Institutional authorization and applicable approvals are required; no specific cohort approval is documented in this repository |
| Galois-generated tables | Configured Azure OpenAI deployment | LLM-generated content intended to represent public knowledge; not verified source records | No patient cohort; diseases, drugs, genes, anatomical sites, hospitals and clinical trials | Configured service terms and research-use conditions apply |
| Automated-test fixtures | Repository test code | Programmatically constructed test tables, mappings and simulated service responses | Artificial test cases, not a documented clinical cohort | Functional testing; no clinical study approval is claimed |
| Bundled ontology and offline imports | HERO and imported ontology resources | Public ontology resources; separate license information is bundled | Not a participant dataset | See `myapp/obda/imports/README` and bundled notices |
| `uploads/level.duckdb` | Repository configuration | Disclosure-level configuration, not a research dataset | Not applicable | Tracked in Git; select the appropriate level before enrollment |

**Release-content scope.** The tracked-file inventory contains no institutional CSV datasets, patient DuckDB database, application SQLite database, runtime private-key file or Azure credentials file. The tracked DuckDB file is documented as disclosure configuration. This inspection is not a forensic certification of all bundled binary dependencies or the repository's full history; an unconditional assertion that every released file contains no personal data or identifiers is not made.

Uploaded CSV files are read and deleted, but imported records remain in the local DuckDB database. Backups and runtime logs must be handled according to their contents.

The mapping bootstrap sends table and column names, column types, template rules and relevant ontology descriptions to the configured LLM. It does not include local row values in the described prompt-building path. Schema names can themselves be sensitive and require review before transmission.

Galois prompts describe public-knowledge tables and columns. The standard Galois mapping excludes patient entities. Generated facts are not independently verified against authoritative sources by this feature.

## 4. Method

- **Algorithm or architecture.** Django administration interface, local DuckDB storage, Ontop virtual knowledge graph and a separate query service. The query service matches catalog templates, validates parameters and enforces the selected maximum disclosure level before querying Ontop.
- **Training setting.** Not applicable: the inspected endpoint does not train or fine-tune a model. Optional features invoke an externally configured Azure OpenAI model.
- **Privacy techniques.** Local source-data storage, catalog restrictions and disclosure levels on the protected query route. Ed25519 signatures and timestamp/nonce checks protect enrollment, catalog and ontology management messages. Query refusals and selected backend failures are normalized to empty responses. These mechanisms do not provide differential privacy, secure aggregation or an anonymization guarantee. The query route itself is unauthenticated, and administration/direct Ontop access bypass its restrictions.

The mapping bootstrap fixes the ontology side of each rule and asks the LLM to select local tables and columns. Suggestions require explicit saving; source queries are validated against local data. Galois refreshes mapped public-knowledge tables through LLM TableScan calls before Ontop evaluates an admitted query. Key-format checks and deduplication do not establish factual correctness.

## 5. Performance

- **Evaluation data.** The repository includes constructed test tables, temporary databases, simulated HTTP/LLM services and optional real-Ontop integration tests. No external clinical validation cohort is documented.
- **Metrics and results.** Tests cover CSV upload behavior, mapping generation and validation, query matching and binding, disclosure handling, signed enrollment and updates, offline imports, Galois behavior and interface code. No quantitative mapping-accuracy, LLM factual-accuracy, privacy-attack or clinical-performance results are documented. The existing README reports checks on Ubuntu 24.04, Python 3.12 and OpenJDK 21. The test suite was not executed during preparation of this README, so those reported checks are not independent verification here.
- **Results by subgroup.** No demographic, institutional or disease-subgroup evaluation results are documented. The release does not establish equivalent performance across such groups.

The mapping bootstrap is documented as tested with a simulated LLM. The Azure client is documented as tested against a simulated Azure service, not live Azure. Optional real-Ontop tests do not establish live Azure integration quality.

## 6. Known limitations and risks

- **Generalizability.** Mapping correctness depends on local schema interpretation, the template and ontology. The default clinical schemas do not establish suitability for other institutions or populations.
- **Known biases and confounders.** Local data coverage, mappings and disclosure settings affect answers. LLM suggestions and generated public-knowledge tables may contain errors or biases; no systematic bias evaluation is documented.
- **Failure modes.** Refused queries, unknown templates and selected backend failures produce normalized empty responses, so Central cannot reliably interpret an empty answer as absence of matching data. Ontology updates can remove mapping blocks and leave Ontop stopped. Partial mappings can omit predicates or entire rules. Galois applies row limits and does not guarantee exhaustive retrieval. Container recreation can lose uploaded data and the active mapping under the current layout.
- **Privacy risks.** Detailed permitted answers can disclose individual-level data. Repeated queries are not covered by a documented formal disclosure guarantee. Schema metadata sent to an LLM may be sensitive even without row values. No membership-inference or model-inversion assessment of the external LLM is documented; those tests are not directly applicable to the endpoint as a non-model release.
- **Deployment security.** Operational administration pages have no authentication; reachable users can modify data, execute SQL and change disclosure settings. Direct Ontop access accepts unrestricted SPARQL. `/sparql-protected/` is also unauthenticated. `DEBUG` is enabled and all hosts are allowed. `SECRET_KEY` is no longer in the code (see *Secrets* below). Initial trust in Central's key is established on first contact. Management signatures do not encrypt traffic.
- **Persistence and configuration.** The selected disclosure level is tracked in Git and initially documented as L4. Runtime institutional data and mappings require separate backup. A network-state volume alone does not preserve the clinical DuckDB database and active site mapping.

## 7. Responsible use and reporting

- **Terms of use.** The project code is distributed under the Apache License, Version 2.0; see LICENSE. Research is the intended use described in Section 2. The research and medical-device notices describe intended use and validation status; they do not amend Apache-2.0 or add field-of-use restrictions to it. Use only authorized data and restrict network access as described below. Do not describe the service as authenticated or clinically validated.
- **LLM use.** Review schema metadata before transmission, review mapping suggestions before saving, and independently verify generated facts used in research conclusions. Protect Azure credentials and HDN signing keys.
- **Web application notice.** The HEREDITARY template requires: “Research prototype of the HEREDITARY project. Not a medical device and not for clinical use. Do not enter personal or patient data.” The administration interface shows it in a bar fixed at the bottom of every page, including the sign-in and first-start pages (`myapp/templates/myapp/_research_notice.html`, included by `base.html`, `login.html` and `setup.html`). This README block and the notice at the top remain the complete statement of research-only use. The public demonstration must remain separate from any institutionally authorized clinical-data deployment.
- **Contact for misuse or vulnerability reports.** Mirco Cazzaro: mirco.cazzaro@phd.unipd.it. Report sensitive findings privately; do not include patient records, credentials or private keys in public issues.

## 8. Acknowledgement

Funded by the European Union under grant agreement No 101137074 (HEREDITARY). Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or the European Health and Digital Executive Agency (HaDEA). Neither the European Union nor the granting authority can be held responsible for them.

## Before you start: network isolation is mandatory

**Deploying this release in an institution requires the network controls below. They are a condition of use, not a recommendation, and this README does not enforce them: the deploying institution must apply and verify them.** The software does not refuse to start on an exposed network.

What the software itself protects, and what it does not:

| Port | Service | Authenticated by the software | Required exposure |
|---|---|---|---|
| 8000 | Administration interface (Django, gunicorn) | Yes: sign-in on every page except the public paths listed in *Access to the administration interface*; the default `admin` password is public until changed | Loopback or an administrative network only. Never to the Internet; remote administration through an SSH tunnel or VPN |
| 8084 | Query service `/sparql-protected/` and signed protocols `/hdn/` | `/hdn/` is signed (Ed25519). **`/sparql-protected/` is not authenticated**: any client that can reach it can run catalog queries up to the local disclosure level | Only the Central host or a trusted network, restricted at the firewall |
| 8080 | Ontop | **No.** It accepts arbitrary SPARQL and bypasses catalog and disclosure checks | Loopback only. Never published, never forwarded by a proxy |

Port 8000 also serves `/sparql-protected/` and `/hdn/` (without the 404 gate that port 8084 applies), so it must not be reachable by Central's network or the Internet either.

Mandatory controls:

1. Allow inbound connections to 8084 only from the Central host or a trusted network. Deny 8000 and 8080 from every other host.
2. Bind 8000 to `127.0.0.1` (the Docker example below does). Ontop and the query service listen on all interfaces by default (the Ontop endpoint is a Spring Boot server with no bind option in this release, and `run_sparql_server.py` binds `""`), so for 8080 a host firewall rule, or a container that does not publish it, is the only control.
3. A reverse proxy must forward only the path of the intended service. A proxy that forwards to 8000 publishes the administration sign-in to every client of that proxy: restrict it by source address, and add TLS (`HDN_HTTPS=1`).
4. Change the default `admin` password at the first sign-in and set `HDN_SECRET_KEY` (see *Secrets*).
5. Do not enter personal or patient data in a deployment that does not satisfy these controls and its own institutional approvals.

Verify the effective configuration after every deployment or network change; a README statement is not evidence. From the endpoint host:

```bash
ss -ltnp | grep -E ':(8000|8080|8084)'     # 8000 must show 127.0.0.1; note 8080 and 8084
docker port hdn-endpoint                       # Docker: only 127.0.0.1:8000 and the 8084 mapping; never 8080
sudo ufw status verbose  # or: sudo iptables -S / nft list ruleset
```

From a host that must **not** have access (not Central, not the administrator), each of these must fail or time out:

```bash
curl -m 5 -i http://ENDPOINT:8000/login/
curl -m 5 -i http://ENDPOINT:8080/sparql
curl -m 5 -i -X POST http://ENDPOINT:8084/sparql-protected/ -d 'query=ASK{}'
```

From Central, `curl -i http://ENDPOINT:8084/sparql-protected/` must return `405`, and `/login/` on port 8084 must return `404`.

Record the result (date, host, rules in force) with the institutional deployment documentation.

## Requirements

| Component | Requirement |
|---|---|
| Operating system | Linux; existing documentation reports Ubuntu 24.04 checks |
| Python | 3.10 or later; Docker uses 3.10 and existing documentation reports installation with 3.12 |
| Java | 11 or later for Ontop; existing documentation reports OpenJDK 21 checks |
| Disk | Approximately 300 MB for the repository, plus dependencies and data |
| Network | Installation requires dependency access. Local-data operation uses bundled UI libraries and ontology imports and communicates with Central. Optional LLM features require access to the configured Azure service. |

On Ubuntu/Debian:

```bash
sudo apt install python3 python3-venv default-jre-headless git
```

## Installation

```bash
git clone https://github.com/mircocazzaro/tdn-endpoint.git
cd tdn-endpoint
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

To run functional tests:

```bash
python manage.py test myapp.tests
```

## Starting the endpoint

| Port | Process | Intended user |
|---|---|---|
| 8000 | Administration interface | Local administrator |
| 8084 | Protected query service (`/sparql-protected/`) and HDN protocols | HDN Central |
| 8080 | Ontop | Local application processes |

From the repository directory, with the virtual environment active, start each service in a separate terminal or through a service manager:

```bash
# Administration interface
gunicorn myproject.wsgi:application --bind 127.0.0.1:8000 --workers 2 --threads 2 --timeout 300

# Query service for Central
python run_sparql_server.py
```

Open `http://localhost:8000/`. Do not start Ontop manually: start and stop it through *Ontop Monitor*. The interface also stops and restarts it when uploading data or saving mappings.

## Initial configuration

### 1. Upload data

In *Upload CSV*, select one or more files. The filename becomes the table name, including spaces and letter case. The default template expects:

| File | Contents |
|---|---|
| `PATIENTS GENERAL DATA.csv` | One row per patient: demographics, onset, diagnosis, genetics and comorbidities |
| `ALS FUNCTIONAL RATING SCALE.csv` | One row per ALSFRS-R visit |
| `SPIRO VISITS.csv` | One row per spirometry visit |

Other names are supported by associating template blocks with the appropriate tables. Re-uploading the same filename replaces its table. If any file fails, none of the tables in that upload are changed. CSV files are deleted after processing; imported records remain in DuckDB. The page displays table schemas.

### 2. Map data to HERO

In *Map Data to HERO*, select a table for each template block, then click each placeholder and its corresponding column. Click *Save*. To map a placeholder to several columns, right-click it, choose *Sum* or *Concatenate* and select the columns in order; the arrows are dashed. A sum accepts numeric columns only. In a concatenation a missing value counts as empty text; in a sum it makes the result missing.

Source queries are executed against local data before saving. A failing block prevents the mapping from being saved and produces an explanation. Numeric columns loaded as text, for example because they contain missing-value codes such as `u`, receive an adjusted validation check and a notification.

Previous mappings are retained in `myapp/obda/mapping-backups/`.

### 3. Select the disclosure level

*Select Privacy Level* sets the maximum catalog level, from L0 (boolean queries) to L6 (full data). Higher-level queries receive normalized empty responses.

**The repository is documented as initially set to L4. Select the appropriate level before enrollment.** Changes apply immediately.

### 4. Start Ontop

In *Ontop Monitor*, enable *Running*. Startup takes a few seconds; the console displays the log.

### 5. Verify locally

*Run SPARQL Query* accesses Ontop directly, bypassing catalog and disclosure checks. Use it to verify mappings:

```sparql
PREFIX bto:  <https://w3id.org/brainteaser/ontology/schema/>
PREFIX NCIT: <http://purl.obolibrary.org/obo/NCIT_>
SELECT (COUNT(DISTINCT ?pat) AS ?n) WHERE {
  ?pat a bto:Patient ;
       bto:hasDisease NCIT:C34373 .
}
```

### 6. Enroll with Central

Verify connectivity from Central:

```bash
curl -i http://endpoint.institution.example:8084/sparql-protected/
# Expected: HTTP/1.0 405 Method Not Allowed
```

In **HDN Network**, enter Central's URL, this endpoint's reachable port-8084 URL and the institution's name. Submit the application. Status remains *Waiting for approval* until Central approves it, then becomes *Member* with a notification. Central can then send queries, catalogs and ontologies.

## HDN protocols

The following management protocols use Ed25519-signed requests and responses:

| Protocol | Path | Action |
|---|---|---|
| Enrollment | `/hdn/enrollment/` | Receives the application outcome |
| Catalog | `/hdn/catalog/` | Installs a newer query catalog |
| Ontology | `/hdn/ontology/` | Installs ontology and template, adjusts mappings and restarts Ontop if previously running |

The endpoint creates its key in `uploads/hdn/identity.pem` on first use. Central's key is trusted on first contact; a changed key at the same URL is refused. Establish the first contact over a trusted network. Signatures bind action, recipient, timestamp and nonce to protect against alteration, replay and redirection.

Only an approved Central can publish catalogs and ontologies. Catalog updates may change template text, levels and descriptions, but cannot introduce new parameter grammars. `myapp/catalog.py` supplies version 0.

Ontology updates include a matching `.obda` template. The bundled `myapp/mappings/template.obda` applies until replaced. Updates are rejected if imports cannot be resolved offline or the template uses undeclared ontology terms. Site mapping blocks using terms removed by an update are dropped; the previous mapping is backed up. If none remain, Ontop stays stopped until the mapping is rebuilt.

Enrollment, update and restart outcomes appear in **Notifications**. These signed protocols do not authenticate `/sparql-protected/`.

## Query protocol: hdn-components v1

`POST /sparql-protected/` keeps its form fields (`query`, optional `template`). Central sends the *mother* query; the endpoint authorizes it, splits it into components and answers `application/json` with the compact v1 envelope (`protocol: "hdn-components"`, `version: 1`, `query_sha256`, `context`, `plan`, `plan_sha256`, `tuple_pool`, `memberships`). The contract is `HDN_protocollo_componenti_v1.md`; the code is `myapp/hdn_components.py` (contract, encoder, decoder/validator), `myapp/hdn_planner.py` (profile and planner), `myapp/hdn_service.py` (authorization, execution, response) and `myapp/rewriting/` (port of Central's rewriting verifier).

- **Authorization.** The mother must be an instance of an active catalog template (the existing match and most-restrictive-level rule) or have an equivalent rewriting over the active catalog (Compose); its level is the highest among the rewriting's templates and must not exceed the local level. After that, every derived component is authorized (experimental project decision).
- **Components.** The plan comes from the RDFLib 7.6.0 algebra of the mother and follows `hdn theory.pdf` (anchor graph, sections 4.2 and 5.2). Atoms that share an *anchored* variable (one in a local position) or local constants of the same site stay in one component, so the join stays in the site and patient correlation is preserved. Atoms linked only by shared values (codes, drugs, ages) become separate components that Central joins across sites. Reference atoms (replicated predicates) form their own components. The local queries are rebuilt from the plan's atoms, never from the received text.
- **Export.** A site-unique component exports only its interface variables (used by other components, the head, filters, aggregates or other BGPs) with the count of each binding (eager counting, section 6.4); reference and unanchored components export every variable and Central deduplicates copies across sites.
- **Profile.** The federation profile is an optional `HDN_STATE_DIR/profile.json`: `{"version": 1, "reference_predicates": ["https://example.org/broader"], "local_positions": [{"predicate": "https://example.org/takes", "index": 1}], "site_prefixes": [{"site": 1, "prefix": "https://example.org/site/A/"}]}`. `index` 1 is the subject and 2 the object; `"predicate": "*"` means every predicate. Without a file, the subject of every predicate is local and there are no reference predicates. The profile digest is part of the response `context`, so a different profile is visible to Central.
- **No contribution.** Refusal, no match and any failure (backend error, mid-run failure, state changed during the run, blank nodes, more than `HDN_MAX_COMPONENT_ROWS` rows) return the same public envelope with empty `tuple_pool` and `memberships`; the plan is `null` only for queries outside the supported fragment. Reasons are in the audit log.
- **Compatibility.** `analytics_key` requests keep the historic response until an adapter is agreed with Central. `HDN_RESPONSE_FORMAT=legacy` restores the historic SPARQL JSON response for every request, for a gradual rollout.
- **Fixtures.** `myapp/tests/fixtures/hdn_components/` holds serialized queries, plans, site responses and expected results that Central can consume without this endpoint's backend. `HDN_UPDATE_FIXTURES=1` regenerates them.

Known limitations of v1: SPARQL outside the supported fragment (EXISTS/NOT EXISTS, property paths, blank nodes in patterns, GRAPH/SERVICE, non-SELECT/ASK forms) yields `plan: null`; the Compose verifier covers only the rewriting fragment of Central (basic graph patterns, comparison filters, simple aggregates; OPTIONAL, UNION, BIND and subqueries pass only as template instances); `evaluation_query` is the mother unchanged, so ontology reasoning (OWL 2 QL) is left to the backend inside each component and no ontology rewriting is declared; the state check covers files local to the endpoint, so changes made inside an external GraphDB during a request are not detected; component order within a BGP follows list-of-strings comparison of atom ids (`b0.a10` before `b0.a2`).

## LLM-assisted mapping bootstrap

**Bootstrap with an LLM** in *Map Data to HERO* implements the `mbg_bootstrap` source-prediction approach: classes, predicates, IRI templates and reference SQL remain fixed, while the LLM proposes local tables and columns.

Azure settings are shared with Galois and stored in `uploads/hdn/azure.json` with permissions `0600`. The API key is not returned to the interface. Remote endpoints require HTTPS; the code permits localhost HTTP for a local proxy or testing.

Prompts include schema metadata, template rules and relevant ontology descriptions, without local row values. Rules are requested in groups of eight; truncated groups are split and retried. Invented tables, invented columns and columns from another table are rejected. An unassigned placeholder can fall back to a same-named column.

Partial rules omit predicates whose placeholders remain unassigned. If a missing placeholder is needed in the subject or a filter, the rule is omitted. Suggestions populate only rules without saved associations, marked *LLM* or *LLM, partial*. Nothing is saved until *Save* performs normal validation.

Suggestions expire if the schema or template changes. *Discard suggestions* removes them. Quality depends on the external model; the documented bootstrap tests use a simulated LLM.

## Galois mode

A *galois-endpoint* supplies LLM-derived public-knowledge tables without institutional patient data. The integration follows [Galois](https://github.com/mircocazzaro/galois_repro_resources).

On **Galois**, configure Azure, use *Test Azure connection*, select tables and columns, enable *Galois mode* and save. Keys remain enabled. A running Ontop instance is restarted.

| Table | Key / IRI | Optional columns |
|---|---|---|
| `disease` | NCIT code (`NCIT:C34373`) | `name`, `description` |
| `drug` | ATC code (`UATC:N07XX02`) | `name`, `parent_atc_code` |
| `gene` | NCIT code | `name`, `description` |
| `anatomical_site` | NCIT code | `name` |
| `hospital` | Website | `name` |
| `clinical_trial` | NCT identifier (`https://clinicaltrials.gov/study/NCT...`) | `title`, `description`, `disease_ncit_code`, `hospital_website` |

No standard Galois table maps patient entities. The mapping is generated from `myapp/galois/schema.py`; manual *Map Data to HERO* is unavailable. Columns whose terms are absent from the active ontology are excluded with a warning.

For an admitted Central query, the mapping identifies the required tables. Galois refreshes them through TableScan prompts containing `select <columns> from <table>`, without the user SPARQL or Ontop SQL. Ontop then reads the refreshed rows. Failed refreshes do not serve stale results. Queries using no Galois tables do not invoke the LLM.

Rows with absent or malformed keys are discarded, codes are normalized and keys deduplicated. The implementation limits each table scan to 2,000 rows. These checks do not verify facts or guarantee completeness. Ontop also answers from facts already in the ontology.

Parquet files under `uploads/hdn/galois/data/` are exposed through views in `galois.duckdb`, without an Ontop restart. Concurrent requests for the same table share a refresh.

Catalog queries `q15_L0`–`q18_L4` target this mode; `q17` and `q18` require L4. *LLM calls per table* ranges from one to six, default three. Network retries can add requests. Responses can take tens of seconds; Central's query timeout defaults to 240 seconds through `HDN_ENDPOINT_TIMEOUT`.

The documented Azure-client tests use a simulated service rather than live Azure.

## Secrets

Secrets are not versioned. Copy `.env.example` to `.env` (ignored by Git and by the Docker build context) or set the variables in the environment; the environment wins over `.env`. In Docker use `--env-file .env`.

| Variable | Purpose |
|---|---|
| `HDN_SECRET_KEY` | Django signing key. If unset, a random key is generated on first start and stored with `0600` in `HDN_STATE_DIR/secret_key` |
| `HDN_AZURE_ENDPOINT`, `HDN_AZURE_DEPLOYMENT`, `HDN_AZURE_API_VERSION`, `HDN_AZURE_API_KEY` | Azure settings; each overrides the value saved from the Settings page (`azure.json`). Values from the environment are never written to `azure.json` |

The `SECRET_KEY` that was committed in earlier versions is public and must be treated as compromised: do not reuse it.

## Access to the administration interface

The administration interface on port 8000 requires a local account. A fresh database, or one with no user yet, gets the default account **`admin`** with password **`hereditary`**. This password is public (it is in this repository): sign in and change it at once. While it is in use, every page shows a warning with a link to change it; the warning disappears when the password changes. Installations that already have users are not changed by the upgrade. If no user exists, the first visit leads to *First start*, which is closed once an account exists.

Every local user is an administrator. *Settings > Users and password* lists the users, creates new ones, lets each user change their own password (which closes that user's other sessions) and lets any user reset another's password or delete it. The last user and the account in use cannot be deleted. Passwords need at least 10 characters and must pass Django's usual checks: not too common, not numeric only, not similar to the username.

Five wrong passwords for the same username suspend that username for 15 minutes. Sessions last 8 hours and end when the browser closes.

Public paths on port 8000 are the sign-in and first-start pages, the health check `/ontop/status/`, the static files and the protocols that HDN Central calls, `/hdn/` and `/sparql-protected/`. Those protocols keep their own signature and response checks, so the sign-in does not change what Central sees.

## Data sources

*Data source* chooses what the endpoint answers from. One source is active at a time.

| Section | Sources | Queries run through |
|---|---|---|
| Files | CSV (loaded into the local DuckDB), DuckDB file, SQLite file | Ontop and the mapping |
| RDBMS | MySQL, PostgreSQL, Oracle, MS SQL Server, Amazon Aurora | Ontop and the mapping |
| Triple store | GraphDB | GraphDB directly |

With a triple store, Ontop and the mapping are bypassed: HDN Central's query is matched against the catalog and checked against the disclosure level as usual, and only then sent to the GraphDB repository. A refused or failed query returns the same empty answer as before. GraphDB must already hold the data as RDF using the HERO ontology; activating it stops Ontop, and Run Relational Queries, Map Data to HERO and the RDF download are not available. Galois mode needs Ontop, so it cannot be on while a triple store is active. The reasoning GraphDB applies depends on the ruleset of its repository, unlike Ontop's OWL 2 QL rewriting.

The user and password of GraphDB are optional and used as Basic authentication; with a user the server URL must be HTTPS, or a local server. Credentials of every source are stored in `datasources.json` (see below), never shown again and never written in error messages.

## File locations and backups

| Path | Contents | Tracked in Git |
|---|---|---|
| `myapp/obda/mydatabase.duckdb` | Uploaded institutional data | No |
| `myapp/obda/hereditary_ontology_2.obda` | Active site mapping | No |
| `uploads/hdn/datasources.json` | Connections and credentials of the data sources, and the active one | No |
| `uploads/hdn/sources/` | Uploaded SQLite and DuckDB files | No |
| `myapp/obda/hereditary_ontology_2.combinations.json` | Columns and mode of each combined placeholder, for the mapping editor | No |
| `myapp/obda/mapping-backups/` | Last 10 mapping versions | No |
| `uploads/level.duckdb` | Disclosure setting | Yes |
| `uploads/hdn/identity.pem` | Endpoint private key | No |
| `uploads/hdn/db.sqlite3` | Enrollments and notifications | No |
| `uploads/hdn/catalog.json` | Received catalog | No |
| `uploads/hdn/azure.json` | Shared Azure settings and API key | No |
| `uploads/hdn/galois/` | Galois configuration, views, data and mapping | No |
| `uploads/hdn/bootstrap/` | Mapping suggestions | No |
| `uploads/hdn/ontology/` | Received ontology and template, including previous versions | No |
| `audit.log` | Query-service decisions | No |
| `myapp/obda/ontop.log` | Ontop log, rotated at 10 MB with five files | No |
| `myapp/obda/ontop.console.log` | JVM output from the latest startup | No |
| `myapp/mappings/template.obda` | Bundled mapping template | Yes |
| `myapp/catalog.py` | Bundled query catalog | Yes |
| `myapp/obda/hero_clinical.ttl` | HERO ontology | Yes |

Paths under `uploads/hdn/` follow `HDN_STATE_DIR` when overridden. Back up the clinical DuckDB database, active mapping and disclosure setting, plus the relevant network state to preserve keys and configuration. Protect backups according to their contents.

Use `audit.log` to distinguish disclosure refusals, catalog mismatches and backend failures; Central receives normalized non-contribution responses.

## Updating

```bash
git pull
. .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
```

Restart the two application services. Back up local state before updating and verify the disclosure level afterwards: its file is tracked in Git.

Enrolled endpoints receive published catalog updates from Central. `myapp/catalog.py` is the bundled version 0 fallback. Queries missing from the active catalog receive empty responses; new parameter grammars can require a software update.

## Troubleshooting

| Symptom | Likely cause / check |
|---|---|
| Ontop fails with a Java error | Java missing or older than 11; run `java -version` |
| Ontop log reports a mapping error | Regenerate the mapping through *Map Data to HERO* |
| Upload reports “Ontop did not stop in time” | Stop an externally started Ontop process and restart through *Ontop Monitor* |
| Central receives no results | Inspect `audit.log` for level restrictions, catalog mismatch or backend failure |
| “database is held by another process” | Another process holds a DuckDB write lock |
| Port 8084 does not respond | Check `run_sparql_server.py` and network rules |

## Docker

The image uses an unprivileged user and Supervisor for the administration and query services:

```bash
docker build -t hdn-endpoint .
docker run -d --name hdn-endpoint \
  -p 127.0.0.1:8000:8000 \
  -p CENTRAL_FACING_IP:8084:8084 \
  -v hdn-endpoint-uploads:/app/uploads \
  hdn-endpoint
```

Replace `CENTRAL_FACING_IP` with the address of the interface Central reaches; `-p 8084:8084` would publish the unauthenticated query route on every interface, and Docker adds its own firewall rules that bypass `ufw`, so restrict it with the `DOCKER-USER` chain or a network-level firewall. Do not publish port 8080. Start Ontop through *Ontop Monitor*, not the `ontop` program entry in `supervisord.conf`.

The endpoint can be published under any path of the domain (for example `tdn.dei.unipd.it/api1` or `hdn-endpoint.com/abcd`) with no configuration, whether the reverse proxy removes that path or not.

- **Domain `tdn.dei.unipd.it`**: the prefix is `/api<x>`, where `<x>` is the last digit of the port the proxy forwards to (`127.0.0.1:8071` gives `/api1`). The domain comes from `X-Forwarded-Host` and the port from the `Host` the proxy forwards; if that `Host` is the public one (`ProxyPreserveHost On`) there is no backend port and the rule does not apply, so the relative addresses above are used. Set `HDN_PORT_PREFIX_HOSTS` to a comma-separated list of other domains to use the same rule, or to an empty value to turn it off.
- **The proxy removes the path** (Apache `ProxyPass /api1 http://127.0.0.1:8071/`): the application receives `/settings/` and cannot know the prefix, so every address it produces is relative to the page that contains it and the browser resolves it under any prefix. Redirects are relative references too (from `/data/` the sign-in is `../login/`), and links, forms, static files and JavaScript requests use the same form. The root `/` is a small page that adds the trailing slash in the browser and goes to `/home/`, because `/api1` and `/api1/` are the same request for the backend. This does not depend on the `Host` the proxy forwards or on `ProxyPassReverse`, so it works with `ProxyPreserveHost` on or off.
- **The proxy keeps the path**, or sends `X-Forwarded-Prefix`: the prefix is detected from the path, and redirects and links include it.

The proxy must send `X-Forwarded-Host`, which Apache and nginx do by default. Forms are accepted when the browser's origin matches that host, ignoring the scheme; origins of other domains are refused. Behind HTTPS set `HDN_HTTPS=1` and send `X-Forwarded-Proto: https`. Several endpoints on one domain (`/api0`, `/api1`, ...) do not share logins: each instance has its own cookie names, stored in `instance-id` in the state directory.

The `uploads/` volume preserves network state. **It does not preserve institutional data or the active mapping under `myapp/obda/`; those are lost when the container is recreated unless separately preserved.** Do not mount over all of `myapp/obda/`, because that hides the Ontop distribution supplied by updated images.

On a shared Docker network, use container-name URLs such as `http://hdn-central:8000/` and `http://hdn-endpoint:8084/`. Host loopback binding does not restrict access from other containers on that network; keep it trusted.

## Development

```bash
python manage.py runserver 127.0.0.1:8000
python run_sparql_server.py
```

`/sparql-protected/` is also served on port 8000. Optional tests requiring external tools are enabled with:

```bash
HDN_ONTOP_LIVE=1 python manage.py test myapp.tests
HDN_MERMAID_NODE_MODULES=/path/to/node_modules python manage.py test myapp.tests
```

| Variable | Effect |
|---|---|
| `HDN_HTTPS=1` | Secure cookies, HTTPS redirect and HSTS for an HTTPS deployment |
| `HDN_SPARQL_PORT` | Query service port; default 8084 |
| `HDN_AUDIT_LOG` | Audit log path |
| `HDN_STATE_DIR` | Network state and application database directory; default `uploads/hdn` |
| `HDN_ONTOP_OWL_LOG_LEVEL=WARN` | Displays otherwise hidden OWL 2 QL warnings |
| `HDN_RESPONSE_FORMAT` | `components` (default) or `legacy` for `/sparql-protected/` |
| `HDN_COMPONENT_TIMEOUT` | Backend timeout per component, seconds; default 10 |
| `HDN_MAX_COMPONENT_ROWS` | Rows above which the endpoint does not contribute; default 200000 |

## Licenses

HDN Endpoint's own code is distributed under the Apache License, Version 2.0 (`Apache-2.0`); see LICENSE. Bundled components and ontologies retain their own licenses and notices in `myapp/static/myapp/vendor/README`, `myapp/obda/imports/README` and `myapp/obda/copyright/`.