#!/usr/bin/env python3
"""T3 Code → OpenCode cache test, in two steps.

1. Prepare one folder of synthetic files per thread and print the task to paste into each thread:

       python3 t3_test.py prepare --root ~/cache-test-project/t3-cache-test

   In T3 Code, open the folder that contains the test root (~/cache-test-project) as a project. Start one thread per printed
   task, using the OpenCode provider with the model named for that folder:
   `deploy/deepseek-ai/deepseek-v4.1-flash-1017` for deploy-N, `opencode/deepseek-v4.1-flash-free` for free-N.

2. After the threads finish, read each request's token counts from OpenCode's database:

       python3 t3_test.py extract --root ~/cache-test-project/t3-cache-test --out results/t3
"""
import argparse
import json
import os
import pathlib
import re
import sqlite3

from common import records

GROUPS = {"deploy": "deploy/deepseek-ai/deepseek-v4.1-flash-1017", "free": "opencode/deepseek-v4.1-flash-free"}
ROUTE = {"deploy": "deploy", "free": "opencode-free"}
TASK = """This is a synthetic cache measurement. Follow these steps exactly and do nothing else.

Using the read tool, read these files one at a time, in this order, making exactly one read call per step and waiting for each result before the next:
{files}

Do not edit files, run shell commands, search, use skills, or start agents. After the fifth read, reply with only the last record number of each file, as five numbers separated by commas."""


def prepare(root, runs):
    for group, model in GROUPS.items():
        for run in range(runs):
            folder = root / f"{group}-{run}"
            folder.mkdir(parents=True, exist_ok=True)
            for name in "abcde":
                (folder / f"{name}.txt").write_text("\n".join(records(f"{group}{run}{name}", 120)))
            files = "\n".join(f"{i + 1}. {root.name}/{folder.name}/{n}.txt" for i, n in enumerate("abcde"))
            print(f"===== Thread {folder.name} — OpenCode model {model} =====\n{TASK.format(files=files)}\n")


def extract(root, db, out):
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    pattern = re.compile(re.escape(root.name) + r"/(deploy|free)-(\d+)/")
    rows = []
    for (sid,) in c.execute("select id from session_v2"):
        text = " ".join(d for (d,) in c.execute("select data from session_message where session_id=?", (sid,)))
        m = pattern.search(text)
        if not m:
            continue
        steps = c.execute("select time_created,data from session_message where session_id=? and type='assistant' order by seq", (sid,))
        for step, (ts, data) in enumerate(steps):
            d = json.loads(data)
            t = d.get("tokens", {})
            rows.append(dict(layer="t3-opencode", route=ROUTE[m.group(1)], trial=int(m.group(2)), step=step, ts=ts,
                             model=d.get("model"), input=t.get("input", 0), cached=(t.get("cache") or {}).get("read", 0),
                             output=t.get("output", 0), finish=d.get("finish"),
                             tools=[p.get("name") for p in d.get("content", []) if p.get("type") == "tool"]))
    out.mkdir(parents=True, exist_ok=True)
    with (out / "t3-sessions.jsonl").open("w") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)
    print(f"{len({(r['route'], r['trial']) for r in rows})} sessions, {len(rows)} requests → {out / 't3-sessions.jsonl'}")


def main():
    data = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local/share")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("prepare")
    a.add_argument("--root", type=pathlib.Path, required=True)
    a.add_argument("--runs", type=int, default=4)
    b = sub.add_parser("extract")
    b.add_argument("--root", type=pathlib.Path, required=True)
    b.add_argument("--db", type=pathlib.Path, default=pathlib.Path(data) / "opencode/opencode.db")
    b.add_argument("--out", type=pathlib.Path, default=pathlib.Path("results/t3"))
    args = p.parse_args()
    if args.cmd == "prepare":
        prepare(args.root.expanduser().resolve(), args.runs)
    else:
        extract(args.root.expanduser().resolve(), args.db, args.out)


if __name__ == "__main__":
    main()
