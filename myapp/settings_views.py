"""Settings > Endpoint configuration: credenziali dei provider LLM (solo porta 8000).

Ogni provider ha il proprio form. I segreti non tornano mai alla pagina; un
campo segreto lasciato vuoto conserva il valore salvato.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import azure_settings, llm_providers
from .galois.llm import LLMError


def _values(pid, post):
    return {f.name: post.get(f.name, "") for f in llm_providers.fields(pid)}


@require_http_methods(["GET", "POST"])
def settings_view(request):
    if request.method == "POST":
        pid = request.POST.get("provider", "")
        if pid not in llm_providers.PROVIDERS:
            messages.error(request, "Unknown provider.")
            return redirect("settings")
        name = llm_providers.label(pid)
        if request.POST.get("action") == "test":
            try:
                r = llm_providers.client(pid).chat(
                    [{"role": "user", "content": "Reply with the word OK."}])
                messages.success(request, f"{name} answered ({r.usage_tokens} tokens, "
                                          f"{r.latency_s:.1f}s).")
            except LLMError as exc:
                messages.error(request, f"{name} test failed: {exc}")
            return redirect("settings")

        values = _values(pid, request.POST)
        url_field = llm_providers.URL_FIELDS.get(pid)
        if url_field and values.get(url_field) and not azure_settings.valid_endpoint(
                values[url_field].strip()):
            messages.error(request, f"{name}: the URL must be https:// (http only for localhost).")
            return redirect("settings")
        missing = llm_providers.missing(pid, values)
        if missing:
            messages.error(request, f"{name}: missing {', '.join(missing)}.")
            return redirect("settings")
        llm_providers.save(pid, values)
        messages.success(request, f"{name} saved.")
        return redirect("settings")

    cards = []
    for pid in llm_providers.PROVIDERS:
        pub = llm_providers.public(pid)
        cards.append({
            "id": pid,
            "label": llm_providers.label(pid),
            "configured": llm_providers.configured(pid),
            "fields": [{"name": f.name, "label": f.label, "secret": f.secret,
                        "required": f.required, "placeholder": f.placeholder,
                        "textarea": f.textarea,
                        "value": "" if f.secret else pub.get(f.name, ""),
                        "has_value": pub.get(f"has_{f.name}", False)}
                       for f in llm_providers.fields(pid)],
        })
    return render(request, "myapp/settings.html", {"cards": cards})
