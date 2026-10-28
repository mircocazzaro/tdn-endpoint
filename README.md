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
| Owner partner and contact | Author and maintainer: Mirco Cazzaro, mirco.cazzaro.1999@gmail.com. Affiliation: University of Padua (UNIPD). Developed within the EU-funded HEREDITARY project. This attribution does not determine copyright ownership. |
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
- **Deployment security.** Operational administration pages have no authentication; reachable users can modify data, execute SQL and change disclosure settings. Direct Ontop access accepts unrestricted SPARQL. `/sparql-protected/` is also unauthenticated. `DEBUG` is enabled, all hosts are allowed and a fixed `SECRET_KEY` is included in settings. Initial trust in Central's key is established on first contact. Management signatures do not encrypt traffic.
- **Persistence and configuration.** The selected disclosure level is tracked in Git and initially documented as L4. Runtime institutional data and mappings require separate backup. A network-state volume alone does not preserve the clinical DuckDB database and active site mapping.

## 7. Responsible use and reporting

- **Terms of use.** The project code is distributed under the Apache License, Version 2.0; see LICENSE. Research is the intended use described in Section 2. The research and medical-device notices describe intended use and validation status; they do not amend Apache-2.0 or add field-of-use restrictions to it. Use only authorized data and restrict network access as described below. Do not describe the service as authenticated or clinically validated.
- **LLM use.** Review schema metadata before transmission, review mapping suggestions before saving, and independently verify generated facts used in research conclusions. Protect Azure credentials and HDN signing keys.
- **Web application notice.** The HEREDITARY template requires: “Research prototype of the HEREDITARY project. Not a medical device and not for clinical use. Do not enter personal or patient data.” This notice was not found in the inspected application templates. The README disclaimer alone does not implement it. The public demonstration must remain separate from any institutionally authorized clinical-data deployment.
- **Contact for misuse or vulnerability reports.** Mirco Cazzaro: mirco.cazzaro.1999@gmail.com. Report sensitive findings privately; do not include patient records, credentials or private keys in public issues.

## 8. Acknowledgement

Funded by the European Union under grant agreement No 101137074 (HEREDITARY). Views and opinions expressed are however those of the author(s) only and do not necessarily reflect those of the European Union or the European Health and Digital Executive Agency (HaDEA). Neither the European Union nor the granting authority can be held responsible for them.

## Before you start: current security limitations

**Do not expose this version to an untrusted network.** The operational administration interface on port 8000 has no authentication, direct Ontop on port 8080 accepts arbitrary SPARQL, and the query route on port 8084 is unauthenticated. Catalog and disclosure checks apply through `/sparql-protected/`, not through direct Ontop access.

Allow Central to reach only port 8084, and restrict it to the intended Central host or trusted network. Keep ports 8000 and 8080 local using binding and firewall rules. Use an SSH tunnel for remote administration. Replace development security settings before an actual deployment.

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

In *Map Data to HERO*, select a table for each template block, then click each placeholder and its corresponding column. Click *Generate OBDA File*.

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

## LLM-assisted mapping bootstrap

**Bootstrap with an LLM** in *Map Data to HERO* implements the `mbg_bootstrap` source-prediction approach: classes, predicates, IRI templates and reference SQL remain fixed, while the LLM proposes local tables and columns.

Azure settings are shared with Galois and stored in `uploads/hdn/azure.json` with permissions `0600`. The API key is not returned to the interface. Remote endpoints require HTTPS; the code permits localhost HTTP for a local proxy or testing.

Prompts include schema metadata, template rules and relevant ontology descriptions, without local row values. Rules are requested in groups of eight; truncated groups are split and retried. Invented tables, invented columns and columns from another table are rejected. An unassigned placeholder can fall back to a same-named column.

Partial rules omit predicates whose placeholders remain unassigned. If a missing placeholder is needed in the subject or a filter, the rule is omitted. Suggestions populate only rules without saved associations, marked *LLM* or *LLM, partial*. Nothing is saved until *Generate OBDA File* performs normal validation.

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

## File locations and backups

| Path | Contents | Tracked in Git |
|---|---|---|
| `myapp/obda/mydatabase.duckdb` | Uploaded institutional data | No |
| `myapp/obda/hereditary_ontology_2.obda` | Active site mapping | No |
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
  -p 8084:8084 \
  -v hdn-endpoint-uploads:/app/uploads \
  hdn-endpoint
```

Restrict port 8084 to Central or a trusted network. Do not publish port 8080. Start Ontop through *Ontop Monitor*, not the `ontop` program entry in `supervisord.conf`.

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

## Licenses

HDN Endpoint's own code is distributed under the Apache License, Version 2.0 (`Apache-2.0`); see LICENSE. Bundled components and ontologies retain their own licenses and notices in `myapp/static/myapp/vendor/README`, `myapp/obda/imports/README` and `myapp/obda/copyright/`.