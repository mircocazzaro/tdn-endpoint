"""Pagina dell'amministratore per la modalita' Galois (solo porta 8000)."""
import datetime
import re

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

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
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "save":
            endpoint = request.POST.get("azure_endpoint", "").strip()
            deployment = request.POST.get("azure_deployment", "").strip()
            key = request.POST.get("azure_api_key", "")
            new = _form_config(request.POST, cfg)
            has_key = bool(key.strip() or store.load_azure().get("api_key"))
            if new["enabled"] and not (endpoint and deployment and has_key):
                messages.error(request, "Galois mode needs the Azure OpenAI endpoint, the "
                                        "deployment name and the API key.")
                return redirect("galois")
            # La chiave viaggia nell'header: solo HTTPS, salvo un proxy locale.
            if endpoint and not re.match(r"https://|http://(127\.0\.0\.1|localhost)[:/]", endpoint):
                messages.error(request, "The Azure endpoint must be an https:// URL.")
                return redirect("galois")
            store.save_azure(endpoint, deployment, request.POST.get("azure_api_version", ""),
                             key or None)
            _apply(request, new)
            messages.success(request, "Galois mode is on." if new["enabled"]
                             else "Configuration saved. Galois mode is off.")
        elif action == "test":
            try:
                r = store.llm_client().chat([{"role": "user", "content": "Reply with the word OK."}])
                messages.success(request, f"Azure OpenAI answered ({r.usage_tokens} tokens, "
                                          f"{r.latency_s:.1f}s).")
            except (store.GaloisError, LLMError) as exc:
                messages.error(request, f"Azure OpenAI test failed: {exc}")
        elif action.startswith("refresh:"):
            name = action.split(":", 1)[1]
            if name not in schema.BY_NAME or not cfg["tables"][name]["enabled"]:
                messages.error(request, "Unknown or disabled table.")
            else:
                try:
                    st = store.refresh_table(name)
                    messages.success(request, f"{name}: {st['rows']} rows from the LLM "
                                              f"({st['raw_rows']} returned, {st['tokens']} tokens, "
                                              f"{st['calls']} calls).")
                except store.GaloisError as exc:
                    messages.error(request, f"{name}: refresh failed ({exc}).")
        return redirect("galois")

    azure = store.load_azure()
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
        "azure": {"endpoint": azure.get("endpoint", ""), "deployment": azure.get("deployment", ""),
                  "api_version": azure.get("api_version") or "",
                  "has_key": bool(azure.get("api_key"))},
        "max_iter_choices": range(1, store.MAX_ITER + 1),
    })
