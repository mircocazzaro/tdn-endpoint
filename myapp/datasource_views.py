"""Menu "Data source": scelta della sorgente e sua configurazione (solo porta 8000).

Tre sezioni: file (CSV, DuckDB, SQLite), RDBMS (MySQL, PostgreSQL, Oracle,
MS SQL, Aurora) e triple store (GraphDB).
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import datasources as ds
from . import ontop_process, triplestore

KIND_TYPES = {"file", "remote", "triplestore"}

WIDE_FIELDS = {"host", "database", "url"}

# Logo diverso da myapp/sources/<icona>.svg
ICON_FILES = {"graphdb": "myapp/cropped-graphwise_favicon.png"}


def _icon_path(key, icon):
    if key in ICON_FILES:
        return ICON_FILES[key]
    return f"myapp/sources/{icon}.svg" if icon else ""


def _group_of(kind):
    for gid, (_title, kinds) in ds.GROUPS.items():
        if ds.KINDS[kind].kind in kinds:
            return gid
    return "files"


def _cards():
    cards = []
    for key, k in ds.KINDS.items():
        card = {"id": key, "label": k.label, "icon": k.icon, "icon_path": _icon_path(key, k.icon), "type": k.kind, "group": _group_of(key),
                "configured": ds.configured(key), "active": ds.active() == key}
        if k.kind in ("remote", "triplestore"):
            pub = ds.public(key)
            card["fields"] = [{"name": f.name, "label": f.label,
                               "placeholder": f.placeholder or (str(k.default_port or "") if f.name == "port" else ""),
                               "value": pub.get(f.name, ""), "secret": f.secret,
                               "wide": f.name in WIDE_FIELDS, "required": f.required,
                               "has_value": pub.get("has_password", False)}
                              for f in k.fields]
            card["engine"] = pub["engine"]
            card["engines"] = list(ds.AURORA_ENGINES) if key == "aurora" else []
        cards.append(card)
    return cards


def _sections():
    cards = _cards()
    return [{"id": gid, "title": title, "cards": [c for c in cards if c["group"] == gid]}
            for gid, (title, _kinds) in ds.GROUPS.items()]


def _active_label():
    key = ds.active()
    return "Local DuckDB (CSV files)" if key == "csv" else ds.KINDS[key].label


def _activate(request, kind):
    if ds.is_triplestore(kind):
        from .galois import store as galois_store
        if galois_store.enabled():
            messages.error(request, "Galois mode is on and needs Ontop: turn it off before using a "
                                    "triple store.")
            return
    try:
        ds.set_active(kind)
    except ds.DataSourceError as exc:
        messages.error(request, exc.detail or exc.code)
        return
    if ds.is_triplestore(kind):
        # Ontop non serve piu': le query vanno direttamente a GraphDB
        if ontop_process.is_running():
            ontop_process.stop()
        messages.success(request, f"{ds.KINDS[kind].label} is now the source. SPARQL queries, after "
                                  "the privacy check, go straight to it; Ontop and the mapping are not used.")
        return
    messages.success(request, f"{ds.KINDS[kind].label} is now the source for Run Relational "
                              "Queries, Ontop and the mapping.")
    from . import site as site_db
    if not site_db.mapping_matches_site():
        messages.warning(request, "The saved mapping was generated for another dialect: "
                                  "run Map Data to HERO and save it again before Ontop answers.")
    from .views import restart_ontop_for_new_mapping
    restart_ontop_for_new_mapping(request)


def _save_triplestore(request, kind, label):
    values = {f.name: request.POST.get(f.name, "") for f in ds.KINDS[kind].fields}
    missing = ds.missing(kind, values)
    if missing:
        messages.error(request, f"{label}: missing {', '.join(missing)}.")
        return False
    url = values["url"].strip()
    has_credentials = bool(values["user"].strip())
    if not triplestore.valid_url(url, has_credentials):
        messages.error(request, f"{label}: the URL must be http(s), without credentials inside it; "
                                "with a user it must be https (or a local server).")
        return False
    if not triplestore.valid_repository(values["repository"].strip()):
        messages.error(request, f"{label}: the repository name can only contain letters, digits, "
                                "dot, dash and underscore.")
        return False
    ds.save_remote(kind, values)
    return True


@require_http_methods(["GET", "POST"])
def data_sources_view(request):
    if request.method == "GET":
        return render(request, "myapp/data_sources.html", {
            "sections": _sections(), "active": ds.active(), "active_label": _active_label(),
            "active_is_triplestore": ds.active_is_triplestore(),
        })

    kind = request.POST.get("kind", "")
    action = request.POST.get("action", "")
    if action == "activate":
        if kind not in ds.KINDS:
            messages.error(request, "Unknown data source.")
        else:
            _activate(request, kind)
        return redirect("data_sources")
    if kind not in ds.KINDS or kind == "csv":
        messages.error(request, "Unknown data source.")
        return redirect("data_sources")
    label = ds.KINDS[kind].label
    kind_type = ds.KINDS[kind].kind

    if kind_type == "file":
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, f"{label}: choose a file.")
            return redirect("data_sources")
        upload.seek(0)
        head = upload.read(16)
        upload.seek(0)
        if not ds.check_file_header(kind, head):
            messages.error(request, f"{label}: the file is not a {label} database.")
            return redirect("data_sources")
        ds.store_file(kind, upload.chunks())
        if kind == "sqlite":
            nullable = ds.sqlite_nullable_keys()
            if nullable:
                messages.warning(request, "Ontop refuses primary keys that can be NULL: declare "
                                          f"them NOT NULL ({', '.join(nullable)}).")
        try:
            tables = ds.list_tables(kind)
            messages.success(request, f"{label} loaded: {len(tables)} tables"
                                      + (f" ({', '.join(tables[:8])})" if tables else "") + ".")
        except ds.DataSourceError as exc:
            messages.error(request, f"{label} stored, but cannot be read: {exc.detail or exc.code}")
        return redirect("data_sources")

    if kind_type == "triplestore":
        if not _save_triplestore(request, kind, label):
            return redirect("data_sources")
        if action == "test":
            try:
                size = triplestore.test()
                messages.success(request, f"{label} reachable"
                                          + (f": {size} statements." if size is not None else "."))
            except triplestore.TripleStoreError as exc:
                messages.error(request, f"{label} test failed: {exc.detail or exc.code}")
        else:
            messages.success(request, f"{label} connection saved.")
        return redirect("data_sources")

    # remote
    values = {f.name: request.POST.get(f.name, "") for f in ds.REMOTE_FIELDS}
    if kind == "aurora":
        values["engine"] = request.POST.get("engine", "mysql")
    missing = ds.missing(kind, values)
    if missing:
        messages.error(request, f"{label}: missing {', '.join(missing)}.")
        return redirect("data_sources")
    ds.save_remote(kind, values)
    if action == "test":
        try:
            tables = ds.list_tables(kind)
            messages.success(request, f"{label} reachable: {len(tables)} tables"
                                      + (f" ({', '.join(tables[:8])})" if tables else "") + ".")
        except ds.DataSourceError as exc:
            messages.error(request, f"{label} test failed: {exc.detail or exc.code}")
        return redirect("data_sources")

    messages.success(request, f"{label} connection saved.")
    return redirect("data_sources")
