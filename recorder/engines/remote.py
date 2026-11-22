"""Client for the engine containers' job API (engines/jobserver.py)."""
import time

import httpx

from .base import EngineError


def run_job(url, params, log=print, poll=3.0):
    """Queue a job and wait for it. Waits as long as the job runs: GPU jobs are
    never cancelled from here."""
    try:
        r = httpx.post(f"{url}/v1/jobs", json=params, timeout=30)
    except httpx.HTTPError as e:
        raise EngineError(f"engine at {url} not reachable: {e}") from e
    if r.status_code != 200:
        raise EngineError(f"engine refused the job: {r.status_code} {r.text[:300]}")
    jid = r.json()["id"]
    last, misses = None, 0
    while True:
        time.sleep(poll)
        try:
            r = httpx.get(f"{url}/v1/jobs/{jid}", timeout=30)
            misses = 0
        except httpx.HTTPError:
            misses += 1
            if misses > 100:
                raise EngineError(f"engine at {url} stopped answering")
            continue
        if r.status_code == 404:
            raise EngineError("the engine lost the job (was it restarted?)")
        job = r.json()
        if job["state"] != last:
            log(f"{params['op']} on {url.split('//')[-1]}: {job['state']}")
            last = job["state"]
        if job["state"] == "done":
            return {**job["result"], "job_wall_s": job.get("wall_s")}
        if job["state"] == "failed":
            raise EngineError(job.get("error") or "engine job failed")


def health(url):
    try:
        return httpx.get(f"{url}/v1/health", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return {}
