"""Map Data to HERO: LLM bootstrap of the bindings (admin interface, port 8000)."""
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from . import datastore, llm_providers, site
from .bootstrap import run
from .bootstrap.schema import Schema
from .galois.llm import LLMError


def site_schema(path):
    return Schema.from_site(site.tables_columns(path), site.column_types(path))


@require_POST
def bootstrap_view(request):
    from . import views

    action = request.POST.get("action", "run")
    if action == "discard":
        run.discard()
        messages.info(request, "LLM suggestions discarded.")
        return redirect("map_fields")

    try:
        pid = llm_providers.resolve(request.POST.get("llm_provider", ""))
    except LLMError as exc:
        messages.error(request, f"Mapping bootstrap: {exc}.")
        return redirect("map_fields")

    try:
        schema = site_schema(views.DUCKDB_PATH)
    except datastore.DataStoreBusy:
        messages.error(request, views.BUSY_MESSAGE)
        return redirect("map_fields")
    header, blocks = views.template_mapping_blocks({t: [c.name for c in tb.columns]
                                                    for t, tb in schema.tables.items()})
    label = llm_providers.label(pid)
    try:
        suggestions, summary = run.bootstrap(llm_providers.client(pid), header, blocks, schema)
    except run.BootstrapError as exc:
        messages.error(request, f"Mapping bootstrap: {exc}.")
        return redirect("map_fields")
    except LLMError as exc:
        messages.error(request, f"Mapping bootstrap: {label} failed ({exc}).")
        return redirect("map_fields")
    run.save(run.fingerprint(header, blocks, schema), suggestions, summary)
    messages.success(
        request,
        f"{label} proposed a table for {summary['suggested']} of {summary['rules']} rules "
        f"({summary['complete']} with every placeholder bound; {summary['tokens']} tokens, "
        f"{summary['seconds']} s). Rules already mapped keep their saved bindings. "
        "Review the suggestions, then press Save to save.")
    for p in summary["problems"][:10]:
        messages.warning(request, f"Discarded: {p}")
    return redirect("map_fields")
