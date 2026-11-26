"""The LLM on the box (Ollama, or an Indie Box's model endpoint, which speaks
Ollama). Nothing here may call a cloud API: transcripts never leave the box. An
Indie Box can also offer models of remote providers under Ollama-style names;
the recorder asks the box which ones are its own and refuses the others unless
LLM_REMOTE_OK=1.

Calls take the same GPU lock as the engines (data/.gpu.lock), so a summary
never runs on the GPU next to a transcription job. On 2026-11-17 an Ollama
runner hit a GPU page fault while it summarized next to a 36 GB engine job,
and the driver reset the GPU for every service on the box."""
import fcntl
import json
import time
from contextlib import contextmanager

import httpx


@contextmanager
def gpu_lock(settings):
    path = settings.gpu_lock
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class LLMError(RuntimeError):
    pass


_listing = {"at": -1e9, "models": None}


def model_info(settings):
    """What the box says about the model (an Indie Box's GET /indiebox/v1/models:
    local or not, context length), or {} where there is no such listing (plain
    Ollama, an older box)."""
    if _listing["models"] is None or time.monotonic() - _listing["at"] > 300:
        try:
            r = httpx.get(f"{settings.ollama_url}/indiebox/v1/models", timeout=10)
            _listing["models"] = (r.json().get("models") or []) if r.status_code == 200 else []
            _listing["at"] = time.monotonic()
        except (httpx.HTTPError, ValueError, AttributeError):
            return {}
    want = settings.llm_model if ":" in settings.llm_model else settings.llm_model + ":latest"
    return next((m for m in _listing["models"] if m.get("name") in (settings.llm_model, want)), {})


def context_tokens(settings):
    """How much the model reads at once: LLM_CONTEXT, else what the box says, else 64k (aibox1's tag)."""
    return settings.llm_context or model_info(settings).get("context_length") or 65536


def chat_json(settings, system, user, schema, timeout=1800, temperature=0.2):
    """One chat call that must return JSON matching `schema`. Returns (obj, stats)."""
    info = model_info(settings)
    if settings.llm_local_only and info.get("local") is not True and not settings.llm_remote_ok:
        raise LLMError(f"the box does not list {settings.llm_model} as one of its own models (or did not answer), "
                       "and transcripts stay on the box: choose one of the box's models (LLM_MODEL)")
    if info.get("local") is False and not settings.llm_remote_ok:
        where = (info.get("provider") or {}).get("name") or "a remote provider"
        raise LLMError(f"{settings.llm_model} runs at {where}, not on this box, and transcripts stay on the box: "
                       "choose one of the box's own models (LLM_MODEL), or allow this one with LLM_REMOTE_OK=1")
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": schema,
        # No num_ctx here: a different context size makes Ollama load a second copy.
        "options": {"temperature": temperature},
    }
    if settings.llm_send_context and settings.llm_context:
        # an Ollama of one's own (standalone/) may default to a small context: ask for the
        # size the parts were cut for (on a shared box it would load a second copy)
        body["options"]["num_ctx"] = settings.llm_context
    if not settings.llm_think:
        body["think"] = False
    with gpu_lock(settings):
        t0 = time.monotonic()
        r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
        if r.status_code == 400 and "think" in r.text and "think" in body:
            body.pop("think")
            r = httpx.post(f"{settings.ollama_url}/api/chat", json=body, timeout=timeout)
    if r.status_code != 200:
        raise LLMError(f"Ollama {r.status_code}: {r.text[:300]}")
    data = r.json()
    content = data.get("message", {}).get("content", "")
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM did not return JSON: {content[:200]!r}") from e
    stats = {
        "model": data.get("model", settings.llm_model),
        "wall_s": round(time.monotonic() - t0, 2),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "load_s": round((data.get("load_duration") or 0) / 1e9, 2),
        "prompt_s": round((data.get("prompt_eval_duration") or 0) / 1e9, 2),
        "output_s": round((data.get("eval_duration") or 0) / 1e9, 2),
    }
    return obj, stats
