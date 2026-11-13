"""Pagina dell'amministratore per la modalita' Galois (solo porta 8000).

Le credenziali degli LLM stanno in Settings > Endpoint configuration; qui si
sceglie solo il provider predefinito, usato dalle query di HDN Central.
"""
import datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import datasources as ds
from . import llm_providers
from .galois import schema, store
from .galois.llm import LLMError
from .views import OntopDidNotStop, ontop_paused


def _apply(request, cfg):
    """Salva la configurazione e allinea viste e mapping, con Ontop fermo."""
    store.save_config(cfg)
    try:
        with ontop_paused(request):
            excluded = store.apply(cfg)
    except OntopDidNotStop:
        messages.error(request, "Ontop did not stop: the configuration is saved but not applied. "
                                "Stop Ontop from Ontop Monitor and save again.")
        return
    for mid, terms in excluded:
        messages.warning(request, f"{mid} left out of the mapping: the ontology does not "
                                  f"declare {', '.join(terms)}.")


def _form_config(post, cfg):
    cfg = dict(cfg)
    cfg["enabled"] = post.get("enabled") == "on"
    try:
        cfg["max_iter"] = min(max(int(post.get("max_iter", store.DEFAULT_MAX_ITER)), 1),
                              store.MAX_ITER)
    except ValueError:
        pass
    tables = {}
    for t in schema.TABLES:
        tables[t.name] = {
            "enabled": post.get(f"table__{t.name}") == "on",
            "columns": [c.name for c in t.columns if post.get(f"col__{t.name}__{c.name}") == "on"],
        }
    cfg["tables"] = tables
    return cfg


@require_http_methods(["GET", "POST"])
def galois_view(request):
    cfg = store.load_config()
    choices = llm_providers.choices()
    ids = [c["id"] for c in choices]
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "save":
            new = _form_config(request.POST, cfg)
            default = request.POST.get("llm_default", "")
            if len(ids) == 1:
                default = ids[0]
            new["llm_provider"] = default if default in ids else ""
            if new["enabled"] and ds.active_is_triplestore():
                messages.error(request, "Galois mode needs Ontop: the active source is a triple "
                                        "store, which bypasses it. Choose another source in Data source.")
                return redirect("galois")
            if new["enabled"] and not ids:
                messages.error(request, "Galois mode needs an LLM provider: configure one in "
                                        "Settings > Endpoint configuration.")
                return redirect("galois")
            if new["enabled"] and len(ids) > 1 and not new["llm_provider"]:
                messages.error(request, "Galois mode needs a default LLM provider for the "
                                        "queries of HDN Central: choose one.")
                return redirect("galois")
            _apply(request, new)
            messages.success(request, "Galois mode is on." if new["enabled"]
                             else "Configuration saved. Galois mode is off.")
        elif action.startswith("refresh:"):
            name = action.split(":", 1)[1]
            if name not in schema.BY_NAME or not cfg["tables"][name]["enabled"]:
                messages.error(request, "Unknown or disabled table.")
            else:
                try:
                    llm = store.llm_client(request.POST.get("llm_provider", ""))
                    st = store.refresh_table(name, llm=llm)
                    messages.success(request, f"{name}: {st['rows']} rows from the LLM "
                                              f"({st['raw_rows']} returned, {st['tokens']} tokens, "
                                              f"{st['calls']} calls).")
                except (store.GaloisError, LLMError) as exc:
                    messages.error(request, f"{name}: refresh failed ({exc}).")
        return redirect("galois")

    st = store.status()
    tables = []
    for t in schema.TABLES:
        tc = cfg["tables"][t.name]
        s = dict(st.get(t.name, {}))
        if s.get("refreshed"):
            s["refreshed_at"] = datetime.datetime.fromtimestamp(s["refreshed"])
        tables.append({"t": t, "cfg": tc, "status": s,
                       "columns": [{"c": c, "on": c.name in tc["columns"]} for c in t.columns]})
    try:
        mapping, excluded = store.build_mapping(cfg)
    except Exception:  # ontologia illeggibile: la pagina resta utilizzabile
        mapping, excluded = "", []
    return render(request, "myapp/galois.html", {
        "cfg": cfg, "tables": tables, "mapping": mapping, "excluded": excluded,
        "llm_choices": choices,
        "llm_default": cfg.get("llm_provider", ""),
        "max_iter_choices": range(1, store.MAX_ITER + 1),
    })
