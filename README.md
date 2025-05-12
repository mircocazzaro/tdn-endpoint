# TDN - Endpoint

# tdn-endpoint

A Django-based web application that allows users to:

* Upload CSV files and store them in a DuckDB database
* Define field-to-ontology mappings (OBDA)
* Expose a SPARQL endpoint via Ontop
* Run SPARQL queries through a web interface or API
* Control the Ontop SPARQL server (start/stop, view status/logs)

---

## Features

1. **CSV Upload & DuckDB Storage**

   * Upload CSV files via the web UI
   * Store and manage data in a local DuckDB database (`mydatabase.duckdb`)

2. **Field Mapping (OBDA)**

   * Define mappings between CSV fields and ontology classes/properties
   * Templates stored under `myapp/mappings`

3. **SPARQL Endpoint**

   * Start/stop an Ontop-powered SPARQL endpoint from the web UI
   * Configure the OBDA mapping (`.obda`), ontology (`.ttl`), and properties files in `myapp/obda`

4. **Query Interface**

   * Execute SPARQL queries via a dedicated web page (`/sparql/`) or programmatically via HTTP
   * Supports both public (`/sparql/`) and protected (`/sparql-protected/`) endpoints

5. **Server Control & Monitoring**

   * View Ontop process status and logs (`/ontop/status/`, `/ontop/logs/`)
   * Start and stop the SPARQL endpoint (`/ontop-control/`)

---

## Requirements

* Python 3.8+
* Java 11+ (for Ontop CLI)
* Django 4.x
* duckdb (Python package)
* pandas
* requests
* whitenoise

> **Note:** Ontop CLI (binary and scripts) is included in `myapp/obda` but requires Java.

---

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/mircocazzaro/tdn-endpoint.git
   cd tdn-endpoint/my-webapp
   ```

2. **Install Python dependencies**

   ```bash
   pip install Django duckdb pandas requests whitenoise
   ```

3. **Set up the database**

   ```bash
   python manage.py migrate
   ```

4. **Collect static files**

   ```bash
   python manage.py collectstatic
   ```

---

## Configuration

All configuration lives in `myproject/settings.py`:

* `SECRET_KEY` – change to a secure random value
* `DEBUG` – set to `False` in production
* `ALLOWED_HOSTS` – configure your domain or IP addresses
* `ONTOP_SPARQL_ENDPOINT` – URL of the Ontop SPARQL endpoint (default: `http://localhost:8080/sparql`)
* `MEDIA_ROOT` – directory for uploaded files and DuckDB

You can further customize:

* Templates directory (`BASE_DIR/templates`)
* Static files settings (`STATIC_URL`, `STATIC_ROOT`)

---

## Usage

1. **Run the development server**

   ```bash
   python manage.py runserver
   ```

2. **Access the app**

   * Home & upload CSV: `http://127.0.0.1:8000/`
   * Field mapping: `http://127.0.0.1:8000/map-fields/`
   * Ontop control: `http://127.0.0.1:8000/ontop-control/`
   * SPARQL query UI: `http://127.0.0.1:8000/sparql/`

3. **API Endpoints**

   * **Upload CSV**: `POST /upload-csv/`
   * **Get columns**: `GET /get-columns/` (returns JSON)
   * **Run SPARQL**: `POST /sparql/` or `POST /sparql-protected/`
   * **Set inference level**: `POST /set-level/`
   * **ONTOP control**: `POST /ontop-control/` with `action=start|stop`

---

## OBDA Mapping & Ontology

* Templates and mappings: `myapp/mappings/`
* Ontology files: `myapp/obda/*.ttl`
* OBDA files: `myapp/obda/*.obda`
* Properties: `myapp/obda/*.properties`

When you start the SPARQL endpoint, the app runs:

```bash
ontop endpoint \
  -m path/to/mapping.obda \
  -p path/to/hereditary_ontology_2.properties \
  -o path/to/ontop.log
```

---

## Logs & Monitoring

* **Ontop logs**: `myapp/obda/ontop.log`
* **Ontop status**: Check via `/ontop/status/`

---

## Static Files

Static assets are in `static/` and `myapp/static/`. Collected by `collectstatic` into `staticfiles/`.

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/x`)
3. Commit your changes (`git commit -m "Add feature x"`)
4. Push to the branch (`git push origin feature/x`)
5. Open a pull request

---

## License

This project does not currently include a license. Add one if you wish to specify reuse terms.
