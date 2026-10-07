# myapp/views.py
import os
import json
import logging
import re
import duckdb
import hashlib
import requests
import tempfile
import pandas as pd

from contextlib import contextmanager

from . import catalog, datastore, ontop_process
from .logtail import tail_lines
from .schema_diagram import er_diagram
from .sparql_results import empty_result
from .obda_mapping import (
    adapt_isnan,
    split_collection,
    substitute_identifiers,
    substitute_target_placeholders,
    unresolved_placeholders,
)

# Audit log locale. Registra le decisioni di disclosure che verso l'esterno
# restano invisibili: senza questo, un endpoint che rifiuta tutto e' indistin-
# guibile da un endpoint senza dati anche per il suo stesso amministratore.
audit = logging.getLogger('hdn.audit')

from django import forms
from django.conf import settings
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils.text import slugify

# Percorsi. Una sola definizione: prima erano dichiarati due volte in questo
# modulo, e DUCKDB_PATH valeva uploads/mydatabase.duckdb o
# myapp/obda/mydatabase.duckdb a seconda della riga in cui lo si leggeva. I
# percorsi di Ontop vengono da ontop_process, che li usa per avviarlo.
ONTOP_DIR     = str(ontop_process.ONTOP_DIR)
OBDA_FILE     = str(ontop_process.OBDA_FILE)
LOG_FILE      = str(ontop_process.LOG_FILE)
# Il database dei dati e' quello che Ontop apre: jdbc.url in
# hereditary_ontology_2.properties e' relativo a ONTOP_DIR.
DUCKDB_PATH   = os.path.join(ONTOP_DIR, 'mydatabase.duckdb')
TEMPLATE_OBDA = os.path.join(os.path.dirname(__file__), 'mappings', 'template.obda')

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


def safe_referer(request, fallback='home'):
    """Referer della richiesta se punta a questo host, altrimenti ``fallback``."""
    from django.urls import reverse
    from django.utils.http import url_has_allowed_host_and_scheme

    target = request.META.get('HTTP_REFERER', '')
    if target and url_has_allowed_host_and_scheme(
            target, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return target
    return reverse(fallback)


BUSY_MESSAGE = ("The data database is held by another process and could not be "
                "read. If Ontop was started outside this interface, stop it from "
                "Ontop Monitor and retry.")


class OntopDidNotStop(RuntimeError):
    """Ontop non si e' arrestato entro il tempo previsto."""


@contextmanager
def ontop_paused(request):
    """Arresta Ontop, se acceso, per la durata di una scrittura sui dati, poi lo riavvia.

    Ontop tiene il database aperto in sola lettura, e DuckDB non ammette una
    scrittura mentre un altro processo lo ha aperto. Ogni passo e' notificato
    all'amministratore con un toast. Il riavvio avviene anche se la scrittura
    fallisce, cosi' che l'endpoint federato non resti spento per un errore di
    caricamento.
    """
    was_running = ontop_process.is_running()
    if was_running:
        if not ontop_process.stop():
            raise OntopDidNotStop()
        messages.info(request, "Ontop was running: stopped to update the tables.")
    try:
        yield was_running
    finally:
        if was_running:
            try:
                ontop_process.start()
                ready = ontop_process.wait_ready()
            except Exception as exc:
                audit.warning("ontop-restart-failed error=%s", type(exc).__name__)
                ready = False
            if ready:
                messages.success(request,
                                 "Ontop restarted: the SPARQL endpoint is available again.")
            else:
                messages.error(request,
                               "Ontop did not restart. Check Ontop Monitor and its log: "
                               "the endpoint is not answering HDN Central.")


def home_view(request):
    """
    Home page: upload form + live DuckDB schema.
    """
    try:
        tables_columns = datastore.tables_columns(DUCKDB_PATH)
    except datastore.DataStoreBusy:
        tables_columns = {}
        messages.warning(request, BUSY_MESSAGE)

    # Render, passing schema
    return render(request, 'myapp/home.html', {
        'tables_columns': tables_columns,
        'er_diagram': er_diagram(tables_columns),
    })

def upload_csv_view(request):
    """
    Handles the CSV file upload, converts them into DuckDB tables, and ingests the data.
    """
    if request.method == 'POST' and request.FILES.getlist('csv_files'):
        uploaded_files = request.FILES.getlist('csv_files')

        # Nomi delle tabelle verificati prima di toccare Ontop: un nome non
        # valido non deve costare un arresto e un riavvio dell'endpoint.
        try:
            tables = [datastore.table_name_for(f.name) for f in uploaded_files]
        except datastore.InvalidTableName as exc:
            messages.error(request, f"Upload rejected, no table was updated: {exc}")
            return redirect('home')
        duplicated = sorted({t for t in tables if tables.count(t) > 1})
        if duplicated:
            messages.error(request, "Upload rejected, two files would produce the same "
                                    "table: " + ", ".join(duplicated))
            return redirect('home')

        # I file sono scritti fuori da MEDIA_ROOT, che e' servito via /media/,
        # e rimossi in ogni caso, anche se l'ingestione fallisce.
        with tempfile.TemporaryDirectory(prefix='hdn-upload-') as staging:
            files = []
            for i, (f, table) in enumerate(zip(uploaded_files, tables)):
                target = os.path.join(staging, f"{i}.csv")
                with open(target, 'wb') as out:
                    for chunk in f.chunks():
                        out.write(chunk)
                files.append((table, target, f.name))

            try:
                with ontop_paused(request):
                    try:
                        results = datastore.ingest_csvs(DUCKDB_PATH, files)
                    except datastore.DataStoreBusy:
                        messages.error(request, BUSY_MESSAGE + " No table was updated.")
                    except datastore.IngestError as exc:
                        messages.error(request, f"CSV ingestion failed, no table was updated: {exc}")
                    except Exception as exc:
                        messages.error(request, "CSV ingestion failed, no table was updated "
                                                f"({type(exc).__name__}): {exc}")
                    else:
                        messages.success(request, "Tables updated: " + ", ".join(
                            f"{t} ({action})" for t, action in results) + ".")
            except OntopDidNotStop:
                messages.error(request,
                               "Ontop did not stop in time: no table was updated.")
        return redirect('home')
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
            with datastore.read_connection(DUCKDB_PATH) as conn:
                if conn is None:
                    raise RuntimeError("no data has been uploaded yet")
                df: pd.DataFrame = conn.execute(query_text).df()
                # grab column names
            columns = df.columns.tolist()
            results = df.values.tolist()
        except datastore.DataStoreBusy:
            error = BUSY_MESSAGE
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



def field_mapping_view(request):
    # 1) Parse the OBDA template into header + mapping blocks
    with open(TEMPLATE_OBDA, 'r', encoding='utf-8') as f:
        tpl = f.read()
    header, inner = split_collection(tpl)

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

    # 2) Introspect DuckDB for tables and columns (sola lettura: convive con Ontop)
    try:
        tables_columns = datastore.tables_columns(DUCKDB_PATH)
    except datastore.DataStoreBusy:
        tables_columns = {}
        messages.warning(request, BUSY_MESSAGE)
    
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

        # 3) append any you didn’t already pull from the target. Il confronto
        #    ignora le maiuscole: una colonna 'SEX' del sito e' lo stesso
        #    identificatore del segnaposto 'sex' del template, non un nuovo
        #    segnaposto da associare.
        known = {p.lower() for p in blk['placeholders']}
        for col in extra:
            if col.lower() not in known:
                blk['placeholders'].append(col)
                known.add(col.lower())

    # 3) Parse existing OBDA mappings, extract var→column pairs
    existing = {}
    existing_placeholders = {}
    inner_existing = ''
    if os.path.exists(OBDA_FILE):
        with open(OBDA_FILE, 'r', encoding='utf-8') as f:
            raw_obda = f.read()
        try:
            _, inner_existing = split_collection(raw_obda)
        except ValueError as exc:
            # Un file attivo illeggibile non deve impedire di aprire la pagina:
            # altrimenti l'unico strumento per ripararlo diventa inaccessibile.
            messages.error(
                request,
                f"Mapping attivo non interpretabile ({exc}); la pagina mostra "
                f"il template senza le associazioni salvate."
            )
    if inner_existing:
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
            
    
    # 4) Associazioni salvate per la UI: {mid: {'table': t, 'pairs': {var: col}}}.
    #    Nomi, mai indici, e legate alla tabella a cui si riferiscono: la UI le
    #    ridisegna solo se e' selezionata quella tabella.
    mapping_connections = {}
    for blk in mapping_blocks:
        mid = blk['mappingId']
        saved_table = existing.get(mid, {}).get('table')
        cols = tables_columns.get(saved_table, [])
        saved_vars = existing_placeholders.get(mid, [])
        pairs = {}

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
                pairs[var] = cols[match_idx]

        mapping_connections[mid] = {'table': saved_table if pairs else None,
                                    'pairs': pairs}

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
        problems = []
        generated_sources = []
        adapted_cols = []
        try:
            site_types = datastore.column_types(DUCKDB_PATH)
        except datastore.DataStoreBusy:
            site_types = {}
            problems.append(BUSY_MESSAGE)

        for blk in mapping_blocks:
            mid = blk['mappingId']
            src = blk['source_tpl']
            tgt_inst = blk['target']
            tbl = data.get(f"{mid}__table")
            if not tbl:
                continue
            src = re.sub(r'FROM\s+"[^"]+"', f'FROM "{tbl}"', src)

            raw = request.POST.get(f"connections_{mid}", '') or '{}'
            try:
                parsed = json.loads(raw)
            except ValueError:
                # Il campo nascosto viene azzerato dalla UI durante il
                # caricamento delle colonne: un submit in quella finestra
                # inviava una stringa vuota e faceva fallire la richiesta.
                problems.append(f"{mid}: associazioni non leggibili, riprova")
                continue

            # Il campo contiene {segnaposto: colonna}, per nome. Prima la UI vi
            # mescolava coppie per indice e per nome, e il server provava a
            # interpretare ogni chiave come indice: due voci per lo stesso
            # segnaposto, con l'ordine del dict a decidere quale vinceva.
            cols = tables_columns.get(tbl, [])
            conn_map = {}
            if not isinstance(parsed, dict):
                parsed = {}
            for var_name, col_name in parsed.items():
                if var_name not in blk['placeholders'] or col_name not in cols:
                    problems.append(
                        f"{mid}: associazione {var_name!r} -> {col_name!r} non valida "
                        f"per la tabella {tbl!r}"
                    )
                    continue
                conn_map[var_name] = col_name

            # Sostituzione simultanea e consapevole dei token, sia nel target
            # sia nel source: ogni identificatore viene riscritto una volta
            # sola e mai dentro un literal o un nome di tabella quotato.
            tgt_inst = substitute_target_placeholders(tgt_inst, conn_map)
            src = substitute_identifiers(src, conn_map)

            # isnan() del template presuppone colonne numeriche: dove nel sito
            # la colonna e' testo viene sostituito con un controllo "e' un
            # numero" compatibile con Ontop.
            src, adapted = adapt_isnan(src, site_types.get(tbl, {}))
            if adapted:
                adapted_cols.append(f"{mid} ({', '.join(adapted)})")

            # Nessun blocco viene scritto se il target proietta un segnaposto
            # che il source non produce: e' la condizione che permetteva al
            # file di divergere in silenzio dallo schema locale.
            missing = unresolved_placeholders(tgt_inst, src)
            if missing:
                problems.append(
                    f"{mid}: i segnaposto {missing} non corrispondono a nessuna "
                    f"colonna prodotta dalla query sorgente"
                )
                continue

            lines += [
                f"mappingId\t{mid}",
                # now write out the instantiated target
                f"target\t\t{tgt_inst} ",
                f"source\t\t{src}",
                ""
            ]
            generated_sources.append((mid, src))

        # Ogni source viene eseguito sul database del sito prima di salvare:
        # Ontop lo inoltra verbatim, e un source che non esegue rompe anche le
        # query che lo includono in una union.
        if not problems and generated_sources:
            try:
                failing = datastore.failing_sources(DUCKDB_PATH, generated_sources)
            except datastore.DataStoreBusy:
                failing = {}
                problems.append(BUSY_MESSAGE)
            for mid, error in failing.items():
                problems.append(f"{mid}: la query sorgente non esegue sui dati locali: {error}")

        if problems:
            for problem in problems:
                messages.error(request, problem)
            messages.error(
                request,
                "Mapping non salvato: il file attivo e' stato lasciato invariato."
            )
        else:
            lines.append(']]')
            os.makedirs(ONTOP_DIR, exist_ok=True)
            with open(OBDA_FILE, 'w', encoding='utf-8') as f:
                f.write("\n".join(lines))

            if adapted_cols:
                messages.info(
                    request,
                    "Numeric checks adapted to text columns: " + "; ".join(adapted_cols)
                    + ". Non-numeric values in these columns are treated as missing."
                )
            messages.success(request, "✅ Mappings definition stored!")
            return redirect('map_fields')

    # 8) Build mapping_ui with per-block JSON for the hidden inputs
    mapping_ui = []
    for blk in mapping_blocks:
        mid = blk['mappingId']
        saved = mapping_connections.get(mid, {'table': None, 'pairs': {}})
        mapping_ui.append({
            'mappingId':       mid,
            'mappingLabel':    blk['mappingLabel'],
            'table_field':     form[f"{mid}__table"],
            'placeholders':    blk['placeholders'],
            # Valore iniziale del campo nascosto: le coppie salvate, solo se la
            # tabella selezionata all'apertura e' quella a cui si riferiscono.
            'connections_json': json.dumps(
                saved['pairs'] if form[f"{mid}__table"].value() == saved['table'] else {}),
        })

    # 9) Render the template; le associazioni salvate arrivano via json_script
    return render(request, 'myapp/mapping.html', {
        'mapping_ui':          mapping_ui,
        'mapping_connections': mapping_connections,
        'form':                form,
    })


@require_GET
def get_columns(request):
    """
    AJAX endpoint: given ?table=Foo, return JSON {columns: [...]}
    """
    table = request.GET.get('table')
    if not table:
        return JsonResponse({'columns': []})
    # Introspect DuckDB (sola lettura: convive con Ontop)
    try:
        with datastore.read_connection(DUCKDB_PATH) as conn:
            if conn is None:
                return JsonResponse({'columns': []})
            rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    except datastore.DataStoreBusy:
        return JsonResponse({'columns': [], 'error': BUSY_MESSAGE}, status=503)
    cols = [r[1] for r in rows]  # r = (cid, name, type, ...)
    return JsonResponse({'columns': cols})

def ontop_control_view(request):
    # Avvio e arresto passano da myapp/ontop_process.py, la stessa logica usata
    # quando una scrittura sui dati deve fermare e riavviare Ontop.
    if request.method == 'POST':
        action = request.POST.get('action')
        if action in ('stop', 'restart'):
            ontop_process.stop()
        if action in ('start', 'restart'):
            ontop_process.start()
        return redirect('ontop_control')

    status = 'running' if ontop_process.is_running() else 'stopped'
    return render(request, 'myapp/ontop_control.html', {
        'status': status
    })
    
@require_GET
def ontop_status(request):
    """Return JSON {status: 'running'|'stopped'}."""
    status = 'running' if ontop_process.is_running() else 'stopped'
    return JsonResponse({'status': status})

@require_GET
def ontop_logs(request):
    """Ultime righe del log di Ontop come JSON {lines: [...]}.

    Se l'ultimo avvio e' fallito prima che partisse logback (es. Java assente o
    errore della JVM), il motivo e' solo nella console della JVM: in quel caso,
    cioe' quando la console e' piu' recente del log, le sue ultime righe
    vengono aggiunte in coda.
    """
    lines = tail_lines(LOG_FILE, 200)
    console = str(ontop_process.CONSOLE_FILE)
    try:
        console_newer = (not os.path.exists(LOG_FILE)
                         or os.path.getmtime(console) > os.path.getmtime(LOG_FILE))
    except OSError:
        console_newer = False
    if console_newer:
        extra = tail_lines(console, 50)
        if extra:
            lines += ['----- JVM console (ontop.console.log) -----'] + extra
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

    # Torna alla pagina di provenienza solo se e' su questo host: il Referer e'
    # controllato dal client e senza verifica diventa un open redirect.
    return redirect(safe_referer(request))



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
                headers={'Accept': 'application/sparql-results+json'},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            if 'boolean' in data:
                # Risposta di una ASK: nessuna variabile, un solo booleano.
                sparql_results = {'vars': ['boolean'],
                                  'rows': [[str(data['boolean']).lower()]]}
            else:
                vars_ = data.get('head', {}).get('vars', [])
                rows = [
                    [binding.get(v, {}).get('value', '') for v in vars_]
                    for binding in data.get('results', {}).get('bindings', [])
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
    """Interfaccia di servizio verso HDN Central.

    Il template di una richiesta si ricava dalla query, non da cio' che il
    chiamante dichiara: la query, tolti i valori dei parametri, deve coincidere
    con un template del catalogo, e ogni valore deve rispettare la grammatica
    del proprio tipo (myapp/catalog.py, match_query). Il livello di disclosure
    e' quello del template cosi' ricavato, e a Ontop va la query ricostruita
    dal catalogo con quei valori, mai il testo ricevuto.

    Ogni mancato contributo - query fuori catalogo, rifiuto per livello di
    disclosure, errore o timeout del backend - produce la stessa risposta
    vuota canonica con HTTP 200. Dall'esterno questi casi sono indistinguibili
    fra loro e dall'assenza di dati che matchano, come richiesto da D3.2
    sez. 2.1.1, 2.2.3 e 2.4.4. La causa reale viene registrata solo
    nell'audit log locale.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)

    # Il campo `template` e' una dichiarazione del chiamante e non autorizza
    # nulla; resta accettato perche' Central lo invia, e serve solo all'audit.
    claimed_template = request.POST.get('template', '').strip()
    q = request.POST.get('query', '').strip()
    analytics_key = request.POST.get('analytics_key')

    if not q:
        return JsonResponse({'error': 'Must supply query'}, status=400)

    def no_contribution(reason, **details):
        """Unica uscita negativa dell'endpoint."""
        audit.info(
            "no-contribution reason=%s remote=%s %s",
            reason,
            request.META.get('REMOTE_ADDR', '-'),
            " ".join(f"{k}={v}" for k, v in sorted(details.items())),
        )
        return JsonResponse(empty_result(q))

    # 1-2) Template ricavato dalla query stessa
    try:
        matched = catalog.match_query(q)
    except catalog.RejectedQuery as exc:
        return no_contribution(exc.reason, **exc.details)

    template = matched.template
    allowed_level = template.level
    executed_query = matched.query

    if matched.alternatives:
        audit.warning("ambiguous-match template=%s alternatives=%s",
                      template.key, ",".join(matched.alternatives))
    if claimed_template and catalog.lookup_by_template_text(claimed_template) is not template:
        audit.warning(
            "template-claim-mismatch derived=%s claimed=%s remote=%s",
            template.key,
            hashlib.sha512(claimed_template.encode('utf-8')).hexdigest()[:16],
            request.META.get('REMOTE_ADDR', '-'),
        )

    # 3) Massimo livello di disclosure configurato localmente
    try:
        con = duckdb.connect(settings.LEVEL_DB, read_only=True)
        lvl_row = con.execute(
            "SELECT value FROM options WHERE key='level'"
        ).fetchone()
        con.close()
        local_max_level = int(lvl_row[0][1]) if lvl_row else 0
    except Exception as exc:
        # Fail-closed: in assenza di una policy leggibile si assume la piu'
        # restrittiva. L'evento va registrato perche' altrimenti l'endpoint
        # smetterebbe di contribuire senza che nessuno lo sappia.
        audit.warning("level-store-unreadable error=%s: fallback a L0",
                      type(exc).__name__)
        local_max_level = 0

    if allowed_level > local_max_level:
        return no_contribution('disclosure-refused',
                               requested=allowed_level, local_max=local_max_level)

    # 4) KL-divergence analytics
    if analytics_key == 'klDiv':
        try:
            resp = requests.post(
                settings.ONTOP_SPARQL_ENDPOINT,
                data={'query': executed_query},
                headers={'Accept': 'application/sparql-results+json'},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()

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
            total = (p_counts + q_counts + 2 * eps).sum()
            p = (p_counts + eps) / total
            q_pmf = (q_counts + eps) / total
            kl = float((p * np.log(p / q_pmf)).sum())

            dist_true = [
                {'range': f"{edges[i]:.0f}–{edges[i+1]:.0f}", 'p': float(p[i])}
                for i in range(len(p))
            ]
            dist_false = [
                {'range': f"{edges[i]:.0f}–{edges[i+1]:.0f}", 'p': float(q_pmf[i])}
                for i in range(len(q_pmf))
            ]

            return JsonResponse({
                'distribution_true': dist_true,
                'distribution_false': dist_false,
                'kl_divergence': kl
            })
        except Exception as exc:
            return no_contribution('analytics-error', error=type(exc).__name__)

    # 5) A Ontop va la query ricostruita dal catalogo, non quella ricevuta
    try:
        resp = requests.post(
            settings.ONTOP_SPARQL_ENDPOINT,
            data={'query': executed_query},
            headers={'Accept': 'application/sparql-results+json'},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return no_contribution('backend-error', error=type(exc).__name__)

    audit.info("contributed template=%s level=%s remote=%s",
               template.key, allowed_level, request.META.get('REMOTE_ADDR', '-'))
    return JsonResponse(data)

@require_POST
def delete_table_view(request, table_name):
    """
    Deletes the given table from the DuckDB database if it exists.
    """
    # Verifica preliminare in sola lettura: una tabella inesistente non deve
    # costare un arresto e un riavvio di Ontop.
    try:
        known = datastore.tables_columns(DUCKDB_PATH)
    except datastore.DataStoreBusy:
        messages.error(request, BUSY_MESSAGE)
        return redirect('home')
    if table_name not in known:
        messages.error(request, f"Table `{table_name}` does not exist.")
        return redirect('home')

    try:
        with ontop_paused(request):
            try:
                # Basic safety: only drop known tables
                with datastore.write_connection(DUCKDB_PATH) as conn:
                    existing = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
                    if table_name in existing:
                        conn.execute(f'DROP TABLE "{table_name}"')
                        messages.success(request, f"Tables updated: `{table_name}` deleted.")
                    else:
                        messages.error(request, f"Table `{table_name}` does not exist.")
            except datastore.DataStoreBusy:
                messages.error(request, BUSY_MESSAGE + " No table was deleted.")
    except OntopDidNotStop:
        messages.error(request, "Ontop did not stop in time: no table was deleted.")
    return redirect('home')