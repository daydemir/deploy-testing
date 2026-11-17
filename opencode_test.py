#!/usr/bin/env python3
"""OpenCode CLI cache test.

Runs the same six-turn session (three turns read synthetic files with the read tool, three are short
follow-ups) in a fresh temporary project, once per run and route, then reads each request's token
counts from OpenCode's database. Needs the `opencode` CLI on PATH.

    DEPLOY_API_KEY=... OPENROUTER_API_KEY=... python3 opencode_test.py --out results/opencode
"""
import argparse
import json
import os
import pathlib
import sqlite3
import subprocess
import tempfile
import time

from common import DEPLOY_MODEL, OPENROUTER_MODEL, records

DEPLOY_PROVIDER = {
    "npm": "@ai-sdk/openai-compatible", "name": "Deploy",
    "options": {"baseURL": "https://api.deploy.com/api/v1", "apiKey": "{env:DEPLOY_API_KEY}"},
    "models": {DEPLOY_MODEL: {"name": "DeepSeek V4.1 Flash", "tool_call": True, "reasoning": True,
                              "limit": {"context": 1000000, "output": 32768}}},
}
# route id -> (OpenCode model, project config). Configs reference keys by env var name only.
ROUTES = {
    "deploy": ("deploy/" + DEPLOY_MODEL, {"provider": {"deploy": DEPLOY_PROVIDER}}),
    "deploy-pref128": ("deploy/" + DEPLOY_MODEL, {"provider": {"deploy": DEPLOY_PROVIDER},
                                                  "providers": {"deploy": {"body": {"cache_preference": 128}}}}),
    "opencode-free": ("opencode/deepseek-v4.1-flash-free", {}),
    "openrouter-deepinfra": ("openrouter/" + OPENROUTER_MODEL,
                             {"providers": {"openrouter": {"body": {"provider": {"only": ["deepinfra/fp8"], "allow_fallbacks": False}},
                                                           "settings": {"apiKey": "{env:OPENROUTER_API_KEY}"}}}}),
}
NEEDS = {"deploy": "DEPLOY_API_KEY", "deploy-pref128": "DEPLOY_API_KEY", "openrouter-deepinfra": "OPENROUTER_API_KEY"}
TURNS = [
    "Read a.txt with the read tool, then reply with only the number of records in it. Do not edit files, run commands, use skills or start agents.",
    "Read b.txt with the read tool, then reply with only its first and last record numbers. Do not edit files, run commands, use skills or start agents.",
    "Without using any tools, reply exactly OK.",
    "Read c.txt with the read tool, then reply with only its last record number. Do not edit files, run commands, use skills or start agents.",
    "Without using any tools, reply exactly OK.",
    "Without using any tools, reply exactly DONE.",
]


def default_db():
    data = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local/share")
    return pathlib.Path(data) / "opencode/opencode.db"


def run_session(route, run, db, out):
    model, config = ROUTES[route]
    with tempfile.TemporaryDirectory(prefix="deploy-cache-test-") as tmp:
        root = pathlib.Path(tmp).resolve()
        for name in "abc":
            (root / f"{name}.txt").write_text("\n".join(records(f"{route}-{run}-{name}-{time.time()}", 120)))
        (root / "opencode.json").write_text(json.dumps(config))
        session = None
        for turn, prompt in enumerate(TURNS):
            cmd = ["opencode", "run", "--standalone", "--format", "json", "--model", model, "--archived",
                   "--title", f"deploy cache test {route} {run}"]
            cmd += (["--session", session] if session else []) + [prompt]
            r = subprocess.run(cmd, cwd=root, env=dict(os.environ, PWD=str(root)), capture_output=True, text=True, timeout=300)
            ids = [json.loads(line).get("sessionID") for line in r.stdout.splitlines() if line.startswith("{")]
            session = session or next((i for i in ids if i), None)
            print(f"{route:22} run {run + 1} turn {turn + 1}: exit {r.returncode}", flush=True)
            if r.returncode or not session:
                raise SystemExit(f"OpenCode failed on turn {turn + 1}:\n{r.stderr[-1500:]}")
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        assert pathlib.Path(c.execute("select directory from session_v2 where id=?", (session,)).fetchone()[0]).resolve() == root
        rows = c.execute("select time_created,data from session_message where session_id=? and type='assistant' order by seq",
                         (session,)).fetchall()
        c.close()
    with (out / f"{route}.jsonl").open("a") as f:
        for step, (ts, data) in enumerate(rows):
            d = json.loads(data)
            t = d.get("tokens", {})
            f.write(json.dumps(dict(layer="opencode-cli", route=route, trial=run, step=step, ts=ts, model=d.get("model"),
                                    input=t.get("input", 0), cached=(t.get("cache") or {}).get("read", 0),
                                    output=t.get("output", 0), finish=d.get("finish"),
                                    tools=[p.get("name") for p in d.get("content", []) if p.get("type") == "tool"])) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--routes", nargs="+", default=list(ROUTES), choices=list(ROUTES))
    p.add_argument("--runs", type=int, default=4)
    p.add_argument("--db", type=pathlib.Path, default=default_db(), help="OpenCode database (default: %(default)s)")
    p.add_argument("--out", type=pathlib.Path, default=pathlib.Path("results/opencode"))
    a = p.parse_args()
    missing = sorted({NEEDS[r] for r in a.routes if r in NEEDS and not os.environ.get(NEEDS[r])})
    if missing:
        raise SystemExit("Set " + " and ".join(missing) + " in the environment.")
    a.out.mkdir(parents=True, exist_ok=True)
    for run in range(a.runs):
        for route in a.routes:
            run_session(route, run, a.db, a.out)


if __name__ == "__main__":
    main()
