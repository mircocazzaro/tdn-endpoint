"""Menu "Data source": scelta della sorgente e sua configurazione (solo porta 8000)."""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import datasources as ds

KIND_TYPES = {"file", "remote"}


def _cards():
    cards = []
    for key, k in ds.KINDS.items():
        card = {"id": key, "label": k.label, "icon": k.icon, "type": k.kind,
                "configured": ds.configured(key), "active": ds.active() == key}
        if k.kind == "remote":
            pub = ds.public(key)
            card["fields"] = [{"name": f.name, "label": f.label, "placeholder": f.placeholder or
                               str(k.default_port or ""), "value": pub.get(f.name, ""),
                               "secret": f.secret, "has_value": pub.get("has_password", False)}
                              for f in ds.REMOTE_FIELDS]
            card["engine"] = pub["engine"]
            card["engines"] = list(ds.AURORA_ENGINES) if key == "aurora" else []
        cards.append(card)
    return cards


@require_http_methods(["GET", "POST"])
def data_sources_view(request):
    if request.method == "GET":
        return render(request, "myapp/data_sources.html", {
            "cards": _cards(), "active": ds.active(),
            "active_label": "Local DuckDB (CSV files)" if ds.active() == "csv" else ds.KINDS[ds.active()].label,
        })

    kind = request.POST.get("kind", "")
    action = request.POST.get("action", "")
    if action == "activate":
        if kind not in ds.KINDS:
            messages.error(request, "Unknown data source.")
            return redirect("data_sources")
        try:
            ds.set_active(kind)
        except ds.DataSourceError as exc:
            messages.error(request, exc.detail or exc.code)
            return redirect("data_sources")
        messages.success(request, f"{ds.KINDS[kind].label} is now the source for Run Relational "
                                  "Queries, Ontop and the mapping.")
        from . import site as site_db
        if not site_db.mapping_matches_site():
            messages.warning(request, "The saved mapping was generated for another dialect: "
                                      "run Map Data to HERO and save it again before Ontop answers.")
        from .views import restart_ontop_for_new_mapping
        restart_ontop_for_new_mapping(request)
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
