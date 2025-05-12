# myapp/views.py
import os
import json
import re
import duckdb
import subprocess
import signal
import time
from django import forms
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils.text import slugify 
import requests
import hashlib
import pandas as pd


# Path to the DuckDB file we’re going to create/use
DUCKDB_PATH = os.path.join(settings.MEDIA_ROOT, 'mydatabase.duckdb')
TEMPLATE_OBDA = os.path.join(
     os.path.dirname(__file__),  # this file’s directory → myapp/
     'mappings',
     'template.obda'
)
ONTOP_DIR   = os.path.join(os.path.dirname(__file__), 'obda')
ONTOP_CMD   = os.path.join(ONTOP_DIR, 'ontop')  # or "./ontop" if that’s the executable
OBDA_FILE   = os.path.join(ONTOP_DIR, 'hereditary_ontology_2.obda')
TTL_FILE    = os.path.join(ONTOP_DIR, 'hereditary_ontology_2.ttl')
PROPS_FILE  = os.path.join(ONTOP_DIR, 'hereditary_ontology_2.properties')
PID_FILE    = os.path.join(ONTOP_DIR, 'ontop.pid')
LOG_FILE    = os.path.join(ONTOP_DIR, 'ontop.log')

def normalize_ws(s: str) -> str:
    # replace any run of whitespace (spaces, newlines, tabs, CRs) with a single space
    return re.sub(r'\s+', ' ', s).strip()

def home_view(request):
    """
    Home page: upload form + live DuckDB schema.
    """
    # Try to read the DuckDB file
    tables_columns = {}
    if os.path.exists(DUCKDB_PATH):
        with duckdb.connect(DUCKDB_PATH) as conn:
            # get all tables
            tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
            # for each table, get its columns
            for t in tables:
                info = conn.execute(f"PRAGMA table_info('{t}')").fetchall()
                # PRAGMA table_info returns rows like (cid, name, type, …)
                cols = [col[1] for col in info]
                tables_columns[t] = cols

    # Render, passing schema
    return render(request, 'myapp/home.html', {
        'tables_columns': tables_columns,
    })

def upload_csv_view(request):
    """
    Handles the CSV file upload, converts them into DuckDB tables, and ingests the data.
    """
    if request.method == 'POST' and request.FILES.getlist('csv_files'):
        # Use FileSystemStorage to save the uploaded files
        fss = FileSystemStorage(location=settings.MEDIA_ROOT)
        uploaded_files = request.FILES.getlist('csv_files')
        
        # For each uploaded CSV, store and then ingest into DuckDB
        with duckdb.connect(DUCKDB_PATH) as conn:
            for file in uploaded_files:
                
                filename = fss.save(file.name, file)  # saves file to MEDIA_ROOT
                saved_file_path = os.path.join(settings.MEDIA_ROOT, filename)

                # Ingest CSV into DuckDB (table name can be derived from filename)
                table_name = os.path.splitext(filename)[0].replace('-', '_').replace(' ', '_')
                
                # CREATE or APPEND to a table
                # If we assume new table each time, do CREATE. If it exists, we can overwrite or append.
                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {table_name} AS
                    SELECT * FROM read_csv_auto('{saved_file_path}');
                """
                conn.execute(create_sql)
                
                # Alternatively, if table already exists, you might want to append:
                # insert_sql = f"""
                #     INSERT INTO {table_name}
                #     SELECT * FROM read_csv_auto('{saved_file_path}');
                # """
                # conn.execute(insert_sql)
        messages.success(request, "✅ CSV files correctly uploaded and ingested")
        return redirect('home')  # after success, go back to home or wherever
    return render(request, 'myapp/home.html', {'error': 'No files uploaded'})

def query_view(request):
    """
    Allows the user to input a SQL query and returns JSON or HTML results.
    """
    results = []
    error = None
    query_text = ""
    columns = []

    if request.method == 'POST':
        query_text = request.POST.get('sql_query', '')
        try:
            with duckdb.connect(DUCKDB_PATH) as conn:
                df: pd.DataFrame = conn.execute(query_text).df()
                # grab column names
            columns = df.columns.tolist()
            results = df.values.tolist()
        except Exception as e:
            error = str(e)

    context = {
        'query_text': query_text,
        'results': results,
        'columns': columns,
        'error': error,
    }
    return render(request, 'myapp/query.html', context)

class FieldMappingForm(forms.Form):
    """
    2‑stage form:
      - Always has one <mappingId>__table field per block.
      - Only when bound does it add <mappingId>__<var> fields, required=True.
    """
    def __init__(self, *args, mapping_blocks=None, tables_columns=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Step 1 fields: pick a real table for each mapping block
        for blk in mapping_blocks:
            mid = blk['mappingId']
            name = f"{mid}__table"
            self.fields[name] = forms.ChoiceField(
                label=f"{mid}",
                choices=[("", "— select table —")] + [(t, t) for t in tables_columns],
                widget=forms.Select(attrs={'id': f'table-select-{mid}', 'class': 'form-select mb-3'}),
            )

        # Step 2 fields: always create the placeholder selects (initially empty)
        for blk in mapping_blocks:
            mid = blk['mappingId']
            for var in blk['placeholders']:
                fname = f"{mid}__{var}"
                self.fields[fname] = forms.ChoiceField(
                    label=f"`{var}` →",
                    choices=[("", "— select column —")],  # will be replaced below
                    required=True,
                    widget=forms.Select(attrs={
                        'class': f'form-select placeholder-{mid} mb-3'
                    }),
                )
        
        # ----------------------------
        # 3) ON POST: rebuild each placeholder’s choices from the chosen table
        if self.is_bound:
            for blk in mapping_blocks:
                mid = blk['mappingId']
                table_field = f"{mid}__table"
                chosen_table = self.data.get(table_field)
                cols = tables_columns.get(chosen_table, [])
                opts = [("", "— select column —")] + [(c, c) for c in cols]

                # overwrite each placeholder field’s choices
                for var in blk['placeholders']:
                    pf = f"{mid}__{var}"
                    self.fields[pf].choices = opts
        # ----------------------------



def field_mapping_view(request):
    # 1) load and split the template into header + raw mapping blocks
    full = open(TEMPLATE_OBDA, 'r').read()
    header, rest = full.split('[MappingDeclaration]', 1)
    # extract just inside the [[ … ]]
    inner = re.search(r'@collection\s*\[\[(.*)\]\]', rest, re.S).group(1)

    # 2) parse each mapping block
    raw_blocks = re.split(r'\n\s*\nmappingId', inner.strip())
    mapping_blocks = []
    for raw in raw_blocks:
        text = raw.strip()
        if not text:
            continue
        if not text.startswith('mappingId'):
            text = 'mappingId ' + text

        # pull out mappingId, target, source
        mid = re.search(r'mappingId\s+(\S+)', text).group(1)
        tgt = re.search(r'target\s+(.*?)\nsource', text, re.S).group(1).strip()
        src = re.search(r'source\s+(.*)', text, re.S).group(1).strip()

        # find the table name in FROM "…"
        m = re.search(r'FROM\s+"([^"]+)"', src)
        table = m.group(1) if m else None

        # find placeholders in the *target* (we use these as our variables)
        vars_ = set(re.findall(r'\{(\w+)\}', tgt))
        mapping_blocks.append({
            'mappingId': mid,
            'target': tgt,
            'source': src,
            'table': table,
            'placeholders': vars_,
        })

    # 3) introspect DuckDB for tables + columns
    conn = duckdb.connect(DUCKDB_PATH)
    tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
    tables_columns = {}
    for t in tables:
        info = conn.execute(f"PRAGMA table_info('{t}')").fetchall()
        # PRAGMA table_info returns (cid, name, type, …)
        tables_columns[t] = [col[1] for col in info]
    conn.close()

    form = FieldMappingForm(
        request.POST or None,
        mapping_blocks=mapping_blocks,
        tables_columns=tables_columns
    )

    # Only when POST *and* valid do we generate the file:
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        form = FieldMappingForm(request.POST,
                                 mapping_blocks=mapping_blocks,
                                 tables_columns=tables_columns)
        if form.is_valid():
            data = form.cleaned_data

            # rebuild .obda
            out = [header.strip(), '[MappingDeclaration] @collection [[']
            for blk in mapping_blocks:
                src = blk['source']
                mid = blk['mappingId']
                # 1) table substitution for this block
                chosen_table = data[f"{mid}__table"]
                # replace whatever FROM "..." was in the template with the user‑picked table
                src = re.sub(
                    r'FROM\s+"[^"]+"',
                    f'FROM "{chosen_table}"',
                    src
                )
                # for each placeholder in this block, do a two‐step replace
                for var in blk['placeholders']:
                # use the mapping ID prefix (same as your form field names)
                    key = f"{blk['mappingId']}__{var}"
                    chosen = data[key]
                    # 1) swap every bare var → chosen
                    src = re.sub(rf'\b{var}\b', chosen, src)
                    # 2) revert the alias: “AS chosen” → “AS var”
                    src = re.sub(rf'\bAS\s+{chosen}\b', f'AS {var}', src)

                out.append(f"mappingId\t{blk['mappingId']}")
                out.append(f"target\t{blk['target']}")
                out.append(f"source\t{src}")
                out.append("")  # blank line

            out.append("]]")
            final_text = "\n".join(out)

            obda_dir = os.path.join(os.path.dirname(__file__), 'obda')
            os.makedirs(obda_dir, exist_ok=True)
            obda_path = os.path.join(obda_dir, 'hereditary_ontology_2.obda')
            with open(obda_path, 'w', encoding='utf-8') as f:
                 f.write(final_text)

            messages.success(request, "✅ Mappings written to hereditary_ontology_2.obda")
            # Redirect back (or to home) with a success message if you like:
            return redirect('map_fields')

    else:
        form = FieldMappingForm(mapping_blocks=mapping_blocks,
                                tables_columns=tables_columns)

     # … after form = FieldMappingForm(…) …
    # build a list of UI blocks, each with its table‐field and placeholder fields
    mapping_ui = []
    for blk in mapping_blocks:
        mid = blk['mappingId']
        table_field = form[f"{mid}__table"]
        # always render them, even on GET
        placeholder_fields = [
            form[f"{mid}__{var}"]
            for var in blk['placeholders']
        ]
        mapping_ui.append({
            'mappingId': mid,
            'table_field': table_field,
            'placeholder_fields': placeholder_fields,
        })
        
    mapping_graph = []
    for blk in mapping_blocks:
        mid = blk['mappingId']
        # which table? either user‐picked (POST) or template default (GET)
        table_sel = (form.cleaned_data.get(f"{mid}__table")
                    if form.is_bound and form.is_valid()
                    else blk['table'])
        for var in blk['placeholders']:
            # which column? either user‐picked or var fallback
            sel = (form.cleaned_data.get(f"{mid}__{var}")
                if form.is_bound and form.is_valid()
                else var)
            # extract the ontology property IRI (e.g. "bto:alive") from target
            m = re.search(r'(\w+:\w+)\s*\{\s*' + re.escape(var) + r'\s*\}', blk['target'])
            prop = m.group(1) if m else var

            # slugify into safe Mermaid IDs
            from_id = slugify(f"{table_sel}_{sel}")
            to_id   = slugify(prop)

            mapping_graph.append({
                'from_id':    from_id,
                'from_label': f"{table_sel}.{sel}",
                'to_id':      to_id,
                'to_label':   prop,
            })

    return render(request, 'myapp/mapping.html', {
        'mapping_ui':         mapping_ui,
        'tables_columns_json': json.dumps(tables_columns),
        'mapping_graph':      mapping_graph,
    })
    
@require_GET
def get_columns(request):
    """
    AJAX endpoint: given ?table=Foo, return JSON {columns: [...]}
    """
    table = request.GET.get('table')
    if not table:
        return JsonResponse({'columns': []})
    # Introspect DuckDB
    conn = duckdb.connect(DUCKDB_PATH)
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    conn.close()
    cols = [r[1] for r in rows]  # r = (cid, name, type, ...)
    return JsonResponse({'columns': cols})

def ontop_control_view(request):
    # check if it’s running
    status = 'stopped'
    pid = None
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read())
            os.kill(pid, 0)
            status = 'running'
        except Exception:
            status = 'stopped'

    if request.method == 'POST':
        action = request.POST.get('action')
        # STOP
        if action == 'stop' and status == 'running':
            os.kill(pid, signal.SIGTERM)
            os.remove(PID_FILE)
            status = 'stopped'
        # START
        if action == 'start' and status != 'running':
            with open(LOG_FILE, 'w') as log:
                proc = subprocess.Popen(
                    [ONTOP_CMD, 'endpoint', '-m', OBDA_FILE, '-t', TTL_FILE, '-p', PROPS_FILE],
                    cwd=ONTOP_DIR,
                    stdout=log, stderr=subprocess.STDOUT
                )
            with open(PID_FILE, 'w') as f:
                f.write(str(proc.pid))
            # give it a second to spin up
            time.sleep(1)
            status = 'running'
        # RESTART
        if action == 'restart':
            if status == 'running':
                os.kill(pid, signal.SIGTERM)
                os.remove(PID_FILE)
            proc = subprocess.Popen(
                [ONTOP_CMD, 'endpoint',
                 '-m', OBDA_FILE,
                 '-t', TTL_FILE,
                 '-p', PROPS_FILE],
                cwd=ONTOP_DIR
            )
            with open(PID_FILE, 'w') as f:
                f.write(str(proc.pid))
            time.sleep(1)
            status = 'running'

        return redirect('ontop_control')

    return render(request, 'myapp/ontop_control.html', {
        'status': status
    })
    
@require_GET
def ontop_status(request):
    """Return JSON {status: 'running'|'stopped'}."""
    status = 'stopped'
    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read())
            os.kill(pid, 0)
            status = 'running'
        except Exception:
            status = 'stopped'
    return JsonResponse({'status': status})

@require_GET
def ontop_logs(request):
    """
    Return the last N lines of ontop.log as JSON {lines: [...]}
    """
    N = 200
    if not os.path.exists(LOG_FILE):
        return JsonResponse({'lines': []})
    with open(LOG_FILE, 'rb') as f:
        # tail N lines efficiently
        f.seek(0, os.SEEK_END)
        end = f.tell()
        size = 1024
        data = b''
        while end > 0 and len(data.splitlines()) <= N:
            step = min(size, end)
            end -= step
            f.seek(end)
            data = f.read(step) + data
        lines = data.splitlines()[-N:]
    # decode safely
    lines = [ln.decode('utf-8', 'ignore') for ln in lines]
    return JsonResponse({'lines': lines})


@require_POST
def set_level(request):
    lvl = request.POST.get('level')
    valid = ["L0 - Boolean Queries", "L1 - Simple COUNT Aggregations", "L2 - Full Aggregations (AVG, ecc.)", "L3 - Grouped Data", "L4 - Limited Access to Non‐Sensitive Data", "L5 - Access to Individual Patient Data", "L6 - Full Access to Data"]
    if lvl in valid:
        # write to our LEVEL_DB
        db = settings.LEVEL_DB
        conn = duckdb.connect(db)
        conn.execute("""
           CREATE TABLE IF NOT EXISTS options (
             key TEXT PRIMARY KEY,
             value TEXT
           )
        """)
        # DuckDB supports INSERT OR REPLACE
        conn.execute(
            "INSERT OR REPLACE INTO options VALUES (?, ?)",
            ['level', lvl]
        )
        conn.close()
        messages.success(request, f"Level set to {lvl}")
    else:
        messages.error(request, f"Invalid level: {lvl}")

    # go back
    return redirect(request.META.get('HTTP_REFERER', 'home'))



def sparql_query_view(request):
    """
    Dedicated page for running SPARQL queries against the Ontop VKG.
    """
    sparql_query   = ""
    sparql_results = None
    sparql_error   = None

    if request.method == 'POST':
        sparql_query = request.POST.get('sparql_query', '').strip()
        try:
            # send to your running Ontop SPARQL endpoint
            resp = requests.post(
                settings.ONTOP_SPARQL_ENDPOINT,
                data={'query': sparql_query},
                headers={'Accept': 'application/sparql-results+json'}
            )
            resp.raise_for_status()
            data = resp.json()
            vars_ = data['head']['vars']
            rows = [
                [binding.get(v, {}).get('value', '') for v in vars_]
                for binding in data['results']['bindings']
            ]
            sparql_results = {'vars': vars_, 'rows': rows}
        except Exception as e:
            sparql_error = str(e)

    return render(request, 'myapp/sparql.html', {
        'sparql_query':   sparql_query,
        'sparql_results': sparql_results,
        'sparql_error':   sparql_error,
    })


@csrf_exempt
def protected_sparql(request):
    if request.method != 'POST':
        return JsonResponse({'error':'POST only'}, status=405)

    tmpl = request.POST.get('template','').strip()
    q    = request.POST.get('query','').strip()
    if not tmpl or not q:
        return JsonResponse({'error':'Must supply both template & query'}, status=400)

    # 1) Hash the template exactly
    h = hashlib.sha512(tmpl.encode('utf-8')).hexdigest()

    # 2) Lookup allowed level & stored template
    try:
        con = duckdb.connect(settings.ALLOWED_DB)
        row = con.execute(
            "SELECT level, query FROM allowed_queries WHERE hash = ?",
            [h]
        ).fetchone()
        con.close()
        if not row:
            return JsonResponse({'results':[]})   # template not recognized
        allowed_level, stored_tmpl = row
        stored_tmpl = stored_tmpl.strip()
    except Exception as e:
        return JsonResponse({'error':f'Allowed‐queries DB error: {e}'}, status=500)

    # 3) Verify that the submitted template matches the stored one
    if normalize_ws(tmpl) != normalize_ws(stored_tmpl):
        for i, (c1, c2) in enumerate(zip(tmpl, stored_tmpl)):
            if c1 != c2:
                print(f"First difference at char {i}: {repr(c1)} vs {repr(c2)}")
                break
        return JsonResponse({'error':'Template mismatch'}, status=400)

    # 4) (Optional) sanity‐check that 'query' is a placeholder‐fill of 'tmpl'
    #    e.g. replace all placeholders in tmpl by regex wildcards and match
    #placeholder_pattern = re.escape(tmpl)
    # replace \<name\> in the escaped template with a wildcard
    #placeholder_pattern = re.sub(r'\\<([^>]+)\\>',
    #                             r'.+?',
    #                             placeholder_pattern)
    #placeholder_pattern = r'^\s*' + placeholder_pattern + r'\s*$'
    #if not re.match(placeholder_pattern, q, flags=re.DOTALL):
    #    return JsonResponse({'error':'Query does not fit template'}, status=400)

    # 5) Get user level (as before)
    try:
        con = duckdb.connect(settings.LEVEL_DB)
        lvl_row = con.execute(
            "SELECT value FROM options WHERE key='level'"
        ).fetchone()
        con.close()
        user_level = int(lvl_row[0].lstrip('L')) if lvl_row else 0
    except Exception:
        user_level = 0

    if allowed_level <= user_level:
        print("qui")
        return JsonResponse({'results':[]})

    # 6) Forward the **instantiated** query to Ontop
    try:
        resp = requests.post(
            settings.ONTOP_SPARQL_ENDPOINT,
            data={'query':q},
            headers={'Accept':'application/sparql-results+json'},
            timeout=10
        )
        resp.raise_for_status()
        return JsonResponse(resp.json())
    except Exception as e:
        return JsonResponse({'error':f'Ontop query error: {e}'}, status=502)

