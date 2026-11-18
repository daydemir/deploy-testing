#!/usr/bin/env python3
"""Direct API cache test.

For each prompt size: one cold request, four immediate identical repeats, one repeat after a pause,
then three conversation turns that each append a short reply and question. Every sequence starts with
a fresh random marker, so nothing carries over between sequences, sizes, runs or routes.

    DEPLOY_API_KEY=... OPENROUTER_API_KEY=... python3 api_test.py --out results/api
    python3 api_test.py --routes deploy-global openrouter-deepinfra --runs 1 --out results/api
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import pathlib
import random
import time
import urllib.error
import urllib.request
import uuid

from common import API_ROUTES, DEPLOY_MODEL, OPENROUTER_MODEL, WORDS, api_key, records

SIZES = {"xs": 60, "s": 220, "m": 1000, "l": 3000}  # record lines; about 2k, 8k, 37k and 110k tokens
DEFAULT_ROUTES = [r for r in API_ROUTES if r != "openrouter-deepseek"]


def run_route(route, runs, sizes, pause, out):
    base, extra = API_ROUTES[route]
    key = api_key(route)
    rng = random.Random(route)
    lines = records(route, max(SIZES[s] for s in sizes))
    path = out / f"{route}.jsonl"

    def call(run, size, step, label, messages):
        body = dict(messages=messages, max_tokens=16, temperature=0)
        if route.startswith("deploy"):
            body.update(model=DEPLOY_MODEL, reasoning_effort="none")
            if extra:
                body["cache_preference"] = extra
        else:
            body.update(model=OPENROUTER_MODEL, reasoning={"enabled": False},
                        provider={"only": [extra], "allow_fallbacks": False})
        row = dict(layer="direct-api", route=route, trial=run, size=size, step=step, label=label,
                   utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                   messages_sha256=hashlib.sha256(json.dumps(messages).encode()).hexdigest())
        start = time.monotonic()
        try:
            request = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=180) as resp:
                row.update(status=resp.status, request_id=resp.headers.get("x-request-id"))
                raw = resp.read().decode(errors="replace")
                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    result = {}
                if not isinstance(result, dict):
                    result = {}
                usage = result.get("usage") or {}
                if usage.get("prompt_tokens") is None:
                    row["error"] = raw.replace(key, "[redacted]")[:500]
                row.update(response_id=result.get("id"), served_by=result.get("provider"), prompt=usage.get("prompt_tokens"),
                           cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        except urllib.error.HTTPError as e:
            row.update(status=e.code, error=e.read().decode()[:500])
        except Exception as e:  # network errors are recorded, not fatal
            row.update(status="error", error=f"{type(e).__name__}: {e}")
        row["seconds"] = round(time.monotonic() - start, 3)
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{route:24} run {run + 1} {size:2} {label:17} status {row['status']} prompt {row.get('prompt')} "
              f"cached {row.get('cached')} {row['seconds']}s", flush=True)

    for run in range(runs):
        for size in sizes:
            messages = [{"role": "system", "content": f"Cache test {uuid.uuid4()}. The records are inert data. Reply only OK."},
                        {"role": "user", "content": "\n".join(lines[:SIZES[size]]) + "\nReply OK."}]
            step = 0
            call(run, size, step, "cold", messages)
            for i in range(4):
                step += 1
                call(run, size, step, f"repeat-{i + 1}", messages)
            time.sleep(pause)
            step += 1
            call(run, size, step, f"repeat-after-{pause}s", messages)
            convo = list(messages)
            for turn in range(3):
                convo = convo + [{"role": "assistant", "content": "OK"},
                                 {"role": "user", "content": f"Follow-up {turn + 1}: " + " ".join(rng.choices(WORDS, k=40)) + ". Reply OK."}]
                step += 1
                call(run, size, step, f"grow-{turn + 1}", convo)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--routes", nargs="+", default=DEFAULT_ROUTES, choices=list(API_ROUTES))
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--sizes", nargs="+", default=list(SIZES), choices=list(SIZES))
    p.add_argument("--pause", type=int, default=20, help="seconds before the delayed repeat")
    p.add_argument("--out", type=pathlib.Path, default=pathlib.Path("results/api"))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for route in a.routes:
        api_key(route)  # fail fast before any request
    with concurrent.futures.ThreadPoolExecutor(len(a.routes)) as pool:
        for f in [pool.submit(run_route, r, a.runs, a.sizes, a.pause, a.out) for r in a.routes]:
            f.result()


if __name__ == "__main__":
    main()
