"""The LLM on aibox1 (Ollama). Nothing here may call a cloud API: transcripts
never leave the box.

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
    path = settings.data_dir / ".gpu.lock"
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class LLMError(RuntimeError):
    pass


def chat_json(settings, system, user, schema, timeout=1800, temperature=0.2):
    """One chat call that must return JSON matching `schema`. Returns (obj, stats)."""
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "format": schema,
        # No num_ctx here: a different context size makes Ollama load a second copy.
        "options": {"temperature": temperature},
    }
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
