#!/usr/bin/env python3
"""Everyday-session counters from an OpenCode database, for requests sent to the `deploy` provider.

Exports counts only (no prompt text): for each request, its position in its session, prompt size,
cached tokens and the previous request's prompt size. Session IDs are hashed.

    python3 session_counters.py --since 2026-11-10 --out results/sessions/my-machine.json
    python3 session_counters.py --db path/to/opencode.db --exclude deploy-cache-test- --out ...
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3


def main():
    data = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local/share")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", type=pathlib.Path, nargs="+", default=[pathlib.Path(data) / "opencode/opencode.db"])
    p.add_argument("--provider", default="deploy", help="OpenCode provider ID to count (default: %(default)s)")
    p.add_argument("--since", default="2026-11-10", help="UTC date, inclusive")
    p.add_argument("--exclude", nargs="*", default=["deploy-cache-test-"],
                   help="skip sessions whose directory contains any of these strings (e.g. test folders)")
    p.add_argument("--label", default="", help="machine label stored with each row")
    p.add_argument("--out", type=pathlib.Path, required=True)
    a = p.parse_args()
    since = int(dt.datetime.fromisoformat(a.since).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    out = []
    for db in a.db:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
        dirs = {}
        for table in ("session", "session_v2"):
            if table in tables:
                dirs.update(c.execute(f"select id,directory from {table}"))
        sessions = {}
        q = "select session_id,seq,time_created,data from session_message where type='assistant' and time_created>=? order by session_id,seq"
        for sid, _seq, ts, data in c.execute(q, (since,)):
            d = json.loads(data)
            if d.get("model", {}).get("providerID") != a.provider:
                continue
            if any(x in (dirs.get(sid) or "") for x in a.exclude):
                continue
            t = d.get("tokens", {})
            sessions.setdefault(sid, []).append((ts, t.get("input", 0), (t.get("cache") or {}).get("read", 0)))
        for sid, rows in sessions.items():
            prev = None
            for i, (ts, fresh, cached) in enumerate(rows):
                out.append(dict(host=a.label, session=hashlib.sha256(sid.encode()).hexdigest()[:12], position=i + 1,
                                ts=ts, prompt=fresh + cached, cached=cached, prev_prompt=prev))
                prev = fresh + cached
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out))
    print(f"{len({r['session'] for r in out})} sessions, {len(out)} requests → {a.out}")


if __name__ == "__main__":
    main()
