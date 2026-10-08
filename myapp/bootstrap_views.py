"""Map Data to HERO: LLM bootstrap of the bindings (admin interface, port 8000)."""
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from . import azure_settings, datastore
from .bootstrap import run
from .bootstrap.schema import Schema
from .galois.llm import LLMError


def site_schema(path):
    return Schema.from_site(datastore.tables_columns(path), datastore.column_types(path))


@require_POST
def bootstrap_view(request):
    from . import views

    action = request.POST.get("action", "run")
    if action == "discard":
        run.discard()
        messages.info(request, "LLM suggestions discarded.")
        return redirect("map_fields")

    endpoint = request.POST.get("azure_endpoint", "").strip()
    if endpoint or request.POST.get("azure_api_key", "").strip():
        if not azure_settings.valid_endpoint(endpoint):
            messages.error(request, "The Azure endpoint must be an https:// URL.")
            return redirect("map_fields")
        azure_settings.save(endpoint, request.POST.get("azure_deployment", ""),
                            request.POST.get("azure_api_version", ""),
                            request.POST.get("azure_api_key") or None)
    if not azure_settings.configured():
        messages.error(request, "The mapping bootstrap needs the Azure OpenAI endpoint, the "
                                "deployment name and the API key.")
        return redirect("map_fields")

    try:
        schema = site_schema(views.DUCKDB_PATH)
    except datastore.DataStoreBusy:
        messages.error(request, views.BUSY_MESSAGE)
        return redirect("map_fields")
    header, blocks = views.template_mapping_blocks({t: [c.name for c in tb.columns]
                                                    for t, tb in schema.tables.items()})
    try:
        suggestions, summary = run.bootstrap(azure_settings.client(), header, blocks, schema)
    except run.BootstrapError as exc:
        messages.error(request, f"Mapping bootstrap: {exc}.")
        return redirect("map_fields")
    except LLMError as exc:
        messages.error(request, f"Mapping bootstrap: Azure OpenAI failed ({exc}).")
        return redirect("map_fields")
    run.save(run.fingerprint(header, blocks, schema), suggestions, summary)
    messages.success(
        request,
        f"The LLM proposed a table for {summary['suggested']} of {summary['rules']} rules "
        f"({summary['complete']} with every placeholder bound; {summary['tokens']} tokens, "
        f"{summary['seconds']} s). Rules already mapped keep their saved bindings. "
        "Review the suggestions, then press Generate OBDA File to save.")
    for p in summary["problems"][:10]:
        messages.warning(request, f"Discarded: {p}")
    return redirect("map_fields")
