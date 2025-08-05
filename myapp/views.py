# myapp/views.py
import os
import json
import re
import duckdb
import subprocess
import signal
import time
import hashlib
import requests
import pandas as pd
import sqlparse
from typing import List

from django import forms
from django.conf import settings
from django.shortcuts import render, redirect
from django.core.files.storage import FileSystemStorage
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils.text import slugify

# Path to the DuckDB file we’re going to create/use
DUCKDB_PATH = os.path.join(settings.MEDIA_ROOT, 'mydatabase.duckdb')
TEMPLATE_OBDA = os.path.join(
     os.path.dirname(__file__),  # this file’s directory → myapp/
     'mappings',
     'template.obda'
)
ONTOP_DIR   = os.path.join(os.path.dirname(__file__), 'obda')
ONTOP_CMD   = os.path.join(ONTOP_DIR, 'ontop')  # or "./ontop" if that’s the executable
OBDA_FILE   = os.path.join(ONTOP_DIR, 'hereditary_ontology_2_mappings.ttl')
TTL_FILE    = os.path.join(ONTOP_DIR, 'hero_clinical.ttl')
PROPS_FILE  = os.path.join(ONTOP_DIR, 'hereditary_ontology_2.properties')
PID_FILE    = os.path.join(ONTOP_DIR, 'ontop.pid')
LOG_FILE    = os.path.join(ONTOP_DIR, 'ontop.log')

def normalize_ws(s: str) -> str:
    # replace any run of whitespace (spaces, newlines, tabs, CRs) with a single space
    return re.sub(r'\s+', ' ', s).strip()

def extract_columns_from_sql(sql: str, available_cols: list[str]) -> list[str]:
    """
    Finds all occurrences of any of the available_cols in the SQL text,
    matching as whole‐word (so 'id' doesn’t match 'patient_id_extra').
    """
    low = sql.lower()
    found = []
    for col in available_cols:
        pattern = r'\b' + re.escape(col.lower()) + r'\b'
        if re.search(pattern, low):
            found.append(col)
    return sorted(found)


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
                os.remove(os.path.join(settings.MEDIA_ROOT, filename))
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
    Two-stage form:
      - One <mappingId>__table per block
      - One <mappingId>__<var> per placeholder, choices filled in __init__
    """
    def __init__(self, *args, mapping_blocks=None, tables_columns=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Step 1: table selectors
        for blk in mapping_blocks:
            mid = blk['mappingId']
            self.fields[f"{mid}__table"] = forms.ChoiceField(
                label=mid,
                choices=[("", "— select table —")] +
                        [(t, t) for t in tables_columns],
                required=False,
                widget=forms.Select(attrs={
                    'id': f'table-select-{mid}',
                    'class': 'form-select mb-3'
                }),
            )

        # Step 2: placeholder selectors (empty at first)
        for blk in mapping_blocks:
            mid = blk['mappingId']
            for var in blk['placeholders']:
                self.fields[f"{mid}__{var}"] = forms.ChoiceField(
                    label=f"`{var}` →",
                    choices=[("", "— select column —")],
                    required=False,
                    widget=forms.Select(attrs={
                        'class': f'form-select placeholder-{mid} mb-3'
                    }),
                )

        # Step 3: if bound (POST), refill placeholder choices
        if self.is_bound:
            for blk in mapping_blocks:
                mid = blk['mappingId']
                tbl = self.data.get(f"{mid}__table", "")
                cols = tables_columns.get(tbl, [])
                opts = [("", "— select column —")] + [(c, c) for c in cols]
                for var in blk['placeholders']:
                    self.fields[f"{mid}__{var}"].choices = opts


# Constants for paths
DUCKDB_PATH   = os.path.join(ONTOP_DIR, 'mydatabase.duckdb')
TEMPLATE_OBDA = os.path.join(os.path.dirname(__file__), 'mappings', 'template.obda')
ONTOP_DIR     = os.path.join(os.path.dirname(__file__), 'obda')
OBDA_FILE     = os.path.join(ONTOP_DIR, 'hereditary_ontology_2_mappings.ttl')
TTL_FILE      = os.path.join(ONTOP_DIR, 'hero_clinical.ttl')
PROPS_FILE    = os.path.join(ONTOP_DIR, 'hereditary_ontology_2.properties')
PID_FILE      = os.path.join(ONTOP_DIR, 'ontop.pid')
LOG_FILE      = os.path.join(ONTOP_DIR, 'ontop.log')


def field_mapping_view(request):
    # 1) Parse the OBDA template into header + mapping blocks
    tpl = open(TEMPLATE_OBDA, 'r', encoding='utf-8').read()
    header, rest = tpl.split('[MappingDeclaration]', 1)
    inner = re.search(r'@collection\s*\[\[(.*)\]\]', rest, re.S).group(1)

    mapping_blocks = []
    for raw in re.split(r'\n\s*\nmappingId', inner.strip()):
        txt = raw.strip()
        if not txt:
            continue
        if not txt.startswith('mappingId'):
            txt = 'mappingId ' + txt

        mid = re.search(r'mappingId\s+(\S+)', txt).group(1)
        tgt = re.search(r'target\s+(.*?)\nsource', txt, re.S).group(1).strip()
        src = re.search(r'source\s+(.*)', txt, re.S).group(1).strip()
        default_table = (re.search(r'FROM\s+"([^"]+)"', src) or [None, None])[1]
        # placeholders as list for stable indexing (from {…} in the TARGET)
        vars_ = list(dict.fromkeys(re.findall(r'\{(\w+)\}', tgt)))
        # ── NEW: also grab any filter-only columns (identifiers immediately before “=”) ──
        # 1) columns used with operators (=, <>, IS NULL, IS NOT NULL)
        op_pattern = r'\b([A-Za-z_]\w*)\b\s*(?=(?:=|<>|IS\s+NOT\s+NULL|IS\s+NULL))'
        cols_ops    = re.findall(op_pattern, src, flags=re.IGNORECASE)

        # 2) columns wrapped in isnan(...) calls (optionally preceded by NOT)
        isnan_pattern = r'\bISNAN\s*\(\s*([A-Za-z_]\w*)\s*\)'
        cols_isnan    = re.findall(isnan_pattern, src, flags=re.IGNORECASE)

        # combine, preserving order and uniqueness
        filters = []
        for col in cols_ops + cols_isnan:
            if col not in filters:
                filters.append(col)
        for fcol in filters:
            if fcol not in vars_:
                vars_.append(fcol)
        

        mapping_blocks.append({
            'mappingId':     mid,
            'mappingLabel':  mid,
            'target':        tgt,
            'source_tpl':    src,
            'table_default': default_table,
            'placeholders':  vars_,
        })

    # 2) Introspect DuckDB for tables and columns
    with duckdb.connect(DUCKDB_PATH) as conn:
        tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        tables_columns = {
            t: [c[1] for c in conn.execute(f"PRAGMA table_info('{t}')").fetchall()]
            for t in tables
        }
    
    # 2b) NOW that tables_columns exists, pull out any filter‐only cols
    for blk in mapping_blocks:
        # 1) canonicalize the table name so we actually hit tables_columns
        raw_tbl = blk['table_default'] or ""
        if raw_tbl in tables_columns:
            tbl = raw_tbl
        else:
            alt = slugify(raw_tbl).replace('-', '_')
            tbl = alt if alt in tables_columns else raw_tbl.replace(' ', '_')

        cols = tables_columns.get(tbl, [])

        # 2) regex‐scan the SQL for any of those columns
        
        extra = extract_columns_from_sql(blk['source_tpl'], cols)

        # 3) append any you didn’t already pull from the target
        for col in extra:
            if col not in blk['placeholders']:
                blk['placeholders'].append(col)

    # 3) Parse existing OBDA mappings, extract var→column pairs
    existing = {}
    existing_placeholders = {}
    if os.path.exists(OBDA_FILE):
        raw_obda = open(OBDA_FILE, 'r', encoding='utf-8').read()
        _, body = raw_obda.split('[MappingDeclaration]', 1)
        inner_existing = re.search(r'@collection\s*\[\[(.*)\]\]', body, re.S).group(1)
        for chunk in re.split(r'\n\s*\nmappingId', inner_existing.strip()):
            blk_txt = chunk.strip()
            if not blk_txt:
                continue
            if not blk_txt.startswith('mappingId'):
                blk_txt = 'mappingId ' + blk_txt

            try:
                mid       = re.search(r'mappingId\s+(\S+)', blk_txt).group(1)
                tgt_exist = re.search(r'target\s+(.*?)\n', blk_txt, re.S).group(1).strip()
                saved_vars = list(dict.fromkeys(
                    re.findall(r'\{(\w+)\}', tgt_exist)
                ))
                existing_placeholders[mid] = saved_vars

                src_line  = re.search(r'source\s+(.*)', blk_txt, re.S).group(1).strip()
                tbl = (re.search(r'FROM\s+"([^"]+)"', src_line) or [None, None])[1]

                # build placeholder map exactly as before…
                ph_map = {}
                for blk in mapping_blocks:
                    if blk['mappingId'] != mid:
                        continue
                    for var in blk['placeholders']:
                        # same AS‐alias and positional logic…
                        m = re.search(
                            rf"([^\s,]+)\s+AS\s+{re.escape(var)}\b",
                            src_line
                        )
                        if m:
                            tok = m.group(1).strip()
                            if not (tok.startswith(("'",'"')) and tok.endswith(("'",'"'))):
                                ph_map[var] = tok.strip("'\"")
                            continue
                        elif re.search(rf"\b{re.escape(var)}\b", src_line):
                            ph_map[var] = var
                    missing = [v for v in blk['placeholders'] if v not in ph_map]
                    if missing and len(saved_vars) == len(blk['placeholders']):
                        for i, orig in enumerate(blk['placeholders']):
                            ph_map[orig] = saved_vars[i]
                    break

                # pick up WHERE‐only mappings
                filter_map = {}
                tmpl_where  = re.search(r'WHERE\s+(.*)', blk['source_tpl'], re.S)
                mapped_where = re.search(r'WHERE\s+(.*)', src_line,       re.S)
                if tmpl_where and mapped_where:
                    orig_cols = re.findall(r'(\w+)\s*=', tmpl_where.group(1))
                    new_cols  = re.findall(r'(\w+)\s*=', mapped_where.group(1))
                    if len(orig_cols) == len(new_cols):
                        filter_map = dict(zip(orig_cols, new_cols))

                # ── MERGE those into the main placeholder map ──
                for orig, new in filter_map.items():
                    ph_map[orig] = new

                existing[mid] = {
                    'table':        tbl,
                    'placeholders': ph_map,
                }
            except Exception as e:
                continue
            
    
    # 4) Build positional mapping_connections for the UI (handle missing .obda gracefully)
    mapping_connections = {}
    for blk in mapping_blocks:
        mid = blk['mappingId']
        cols = tables_columns.get(existing.get(mid, {}).get('table'), [])
        saved_vars = existing_placeholders.get(mid, [])
        pos_map = {}

        for idx, var in enumerate(blk['placeholders']):
            # if no existing mapping, info.get('placeholders') is {} → no KeyError
            col = existing.get(mid, {}).get('placeholders', {}).get(var)
            # if still missing, fall back to positional fill
            if col is None and idx < len(saved_vars):
                col = saved_vars[idx]
            if not col:
                continue

            # try to match that column name back to the current table's cols
            match_idx = None
            if col in cols:
                match_idx = cols.index(col)
            else:
                # case‐insensitive
                for j, c in enumerate(cols):
                    if c.lower() == col.lower():
                        match_idx = j
                        break
                # slugified
                if match_idx is None:
                    norm = slugify(col).replace('-', '_')
                    for j, c in enumerate(cols):
                        if slugify(c).replace('-', '_') == norm:
                            match_idx = j
                            break

            if match_idx is not None:
                pos_map[idx] = match_idx

        mapping_connections[mid] = pos_map

    # 5) Prepare initial data for the Django form
    initial = {}
    for blk in mapping_blocks:
        mid = blk['mappingId']
        info = existing.get(mid, {})
        if info.get('table'):
            initial[f"{mid}__table"] = info['table']
        for var in blk['placeholders']:
            if var in info.get('placeholders', {}):
                initial[f"{mid}__{var}"] = info['placeholders'][var]

    # 6) Instantiate the form with mapping_blocks and tables_columns
    form = FieldMappingForm(
        request.POST or None,
        mapping_blocks=mapping_blocks,
        tables_columns=tables_columns,
        initial=initial
    )

    # 7) Handle POST: rebuild OBDA using numeric positional maps
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        lines = [header.strip(), '\n[MappingDeclaration] @collection [[']

        for blk in mapping_blocks:
            mid = blk['mappingId']
            src = blk['source_tpl']
            tgt_inst = blk['target']
            tbl = data.get(f"{mid}__table")
            if not tbl:
                continue
            src = re.sub(r'FROM\s+"[^"]+"', f'FROM "{tbl}"', src)

            raw = request.POST.get(f"connections_{mid}", '{}')
            parsed = json.loads(raw)
            cols = tables_columns.get(tbl, [])
            conn_map = {}
            for key, val in parsed.items():
                try:
                    ph_idx  = int(key)
                    col_idx = int(val)
                    var_name = blk['placeholders'][ph_idx]
                    col_name = cols[col_idx]
                except Exception:
                    var_name, col_name = key, val
                conn_map[var_name] = col_name

            # substitute each placeholder *everywhere* in target & source
            for var, col in conn_map.items():
                # replace every occurrence of {var} (including the braces)
                tgt_inst = tgt_inst.replace(f'{{{var}}}', '{' + col + '}')
                src = src.replace(var + ',' , col + ',')
                src = src.replace(var + ' ' , col + ' ')
                src = src.replace(var + ')' , col + ')')
                src = src.replace('(' + var, '(' + col)

            lines += [
                f"mappingId\t{mid}",
                # now write out the instantiated target
                f"target\t\t{tgt_inst} ",
                f"source\t\t{src}",
                ""
            ]

        lines.append(']]')
        os.makedirs(ONTOP_DIR, exist_ok=True)
        with open(OBDA_FILE, 'w', encoding='utf-8') as f:
            f.write("\n".join(lines))

        messages.success(request, "✅ Mappings definition stored!")
        return redirect('map_fields')

    # 8) Build mapping_ui with per-block JSON for the hidden inputs
    mapping_ui = []
    for blk in mapping_blocks:
        mid = blk['mappingId']
        mapping_ui.append({
            'mappingId':       mid,
            'mappingLabel':    blk['mappingLabel'],
            'table_field':     form[f"{mid}__table"],
            'placeholder_fields': [form[f"{mid}__{v}"] for v in blk['placeholders']],
            'connections_json': json.dumps(mapping_connections.get(mid, {})),
        })

    # 9) Render the template, passing global JSON for reload and per-block JSON for POST
    return render(request, 'myapp/mapping.html', {
        'mapping_ui':               mapping_ui,
        'tables_columns_json':      json.dumps(tables_columns),
        'mapping_connections_json': json.dumps(mapping_connections),
        'form':                     form,
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
    analytics_key = request.POST.get('analytics_key')
    
    #if not tmpl or not q:
    #    return JsonResponse({'error':'Must supply both template & query'}, status=400)

    # 1) Hash the template exactly
    #h = hashlib.sha512(tmpl.encode('utf-8')).hexdigest()

    # 2) Lookup allowed level & stored template
    #try:
    #    con = duckdb.connect(settings.ALLOWED_DB)
    #    row = con.execute(
    #        "SELECT level, query FROM allowed_queries WHERE hash = ?",
    #        [h]
    #    ).fetchone()
    #    con.close()
    #    if not row: 
    #        return JsonResponse({'results':[]})   # template not recognized
    #    allowed_level, stored_tmpl = row
    #    stored_tmpl = stored_tmpl.strip()
    #except Exception as e:
    #    return JsonResponse({'error':f'Allowed‐queries DB error: {e}'}, status=500)

    # 5) Get user level (as before)
    #try:
    #    con = duckdb.connect(settings.LEVEL_DB)
    #    lvl_row = con.execute(
    #        "SELECT value FROM options WHERE key='level'"
    #    ).fetchone()
    #    con.close()
    #    user_level = int(lvl_row[0][1]) if lvl_row else 0
    #except Exception:
    #    user_level = 0

    #if allowed_level > user_level:
    #    print("Quaaaaa")
    #    return JsonResponse({'results':[]})
    
    # 5) KL‐divergence analytics
    if analytics_key == 'klDiv':
        # Forward to Ontop
        try:
            resp = requests.post(
                settings.ONTOP_SPARQL_ENDPOINT,
                data={'query': q},
                headers={'Accept':'application/sparql-results+json'},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            return JsonResponse({'error':f'Ontop query error: {e}'}, status=502)

        # Extract and normalize ages
        bindings = data.get('results', {}).get('bindings', [])
        ages_true, ages_false = [], []
        for bd in bindings:
            bval = bd.get('b', {}).get('value')
            aval = bd.get('ageOn', {}).get('value')
            try:
                age = float(aval)
            except Exception:
                continue
            if str(bval).lower() == 'true':
                ages_true.append(age)
            else:
                ages_false.append(age)

        if not (ages_true or ages_false):
            return JsonResponse({
                'distribution_true': [],
                'distribution_false': [],
                'kl_divergence': None
            })

        # Compute histograms & PMFs
        all_ages = ages_true + ages_false
        bins = np.linspace(min(all_ages), max(all_ages), num=11)
        p_counts, _ = np.histogram(ages_true, bins=bins)
        q_counts, edges = np.histogram(ages_false, bins=bins)
        eps = 1e-9
        total = (p_counts + q_counts + 2*eps).sum()
        p = (p_counts + eps) / total
        q = (q_counts + eps) / total
        kl = float((p * np.log(p / q)).sum())

        dist_true = [
            {'range': f"{edges[i]:.0f}–{edges[i+1]:.0f}", 'p': float(p[i])}
            for i in range(len(p))
        ]
        dist_false = [
            {'range': f"{edges[i]:.0f}–{edges[i+1]:.0f}", 'p': float(q[i])}
            for i in range(len(q))
        ]

        return JsonResponse({
            'distribution_true': dist_true,
            'distribution_false': dist_false,
            'kl_divergence': kl
        })

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

@require_POST
def delete_table_view(request, table_name):
    """
    Deletes the given table from the DuckDB database if it exists.
    """
    # Basic safety: only drop known tables
    with duckdb.connect(DUCKDB_PATH) as conn:
        existing = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        if table_name in existing:
            conn.execute(f'DROP TABLE "{table_name}"')
            messages.success(request, f"✅ Table `{table_name}` deleted.")
        else:
            messages.error(request, f"Table `{table_name}` does not exist.")
    return redirect('home')