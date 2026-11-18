#!/usr/bin/env python3
"""Score every test the same way, print a summary, and write analysis.json, charts and a Deploy request index.

A request "reuses the cache" when at least half of the part that could have been reused came back as
cached tokens: the whole prompt for an identical repeat, the previous request's prompt in a growing
conversation or agent session. It "fully missed" when under 10% did.

    python3 analyze.py                       # reads results/{api,opencode,t3,sessions}
    python3 analyze.py --api results/api --out report
Any input folder that is missing or empty is skipped.
"""
import argparse
import collections
import csv
import json
import pathlib
import statistics

HIT, FULL_MISS = 0.5, 0.1
GREEN, AMBER, RED, INK, MUTED, GRID, PALE = "#15803d", "#d97706", "#dc2626", "#1e293b", "#64748b", "#e2e8f0", "#cbd5e1"
NAMES = {
    "deploy-global": "Deploy (main endpoint)", "deploy-global-pref128": "Deploy (main, cache_preference 128)",
    "deploy-va1": "Deploy (VA1 endpoint)", "deploy-sf1": "Deploy (SF1 endpoint)",
    "openrouter-deepinfra": "DeepInfra (via OpenRouter)", "openrouter-fireworks": "Fireworks (via OpenRouter)",
    "openrouter-together": "Together (via OpenRouter)", "openrouter-nebius": "Nebius (via OpenRouter)",
    "openrouter-parasail": "Parasail (via OpenRouter)", "openrouter-deepseek": "DeepSeek (via OpenRouter)",
    "deploy": "Deploy", "deploy-pref128": "Deploy (cache_preference 128)", "opencode-free": "OpenCode's free DeepSeek",
}
ORDER = list(NAMES)
SIZES = {"xs": "~2k tokens", "s": "~8k tokens", "m": "~37k tokens", "l": "~110k tokens"}


def name(route):
    return NAMES.get(route, route)


def ordered(routes):
    return sorted(routes, key=lambda r: (not r.startswith("deploy"), ORDER.index(r) if r in ORDER else len(ORDER), r))


def load_jsonl(folder):
    rows = []
    for f in sorted(folder.glob("*.jsonl")) if folder and folder.is_dir() else []:
        rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    return rows


def ratio(cached, reusable):
    return min(cached, reusable) / reusable if reusable else None


def pct(n, d):
    return round(100 * n / d, 1) if d else None


def med(xs):
    return round(statistics.median(xs), 1) if xs else None


def hit_stats(qs):
    return dict(n=len(qs), hits=sum(q >= HIT for q in qs), hit_pct=pct(sum(q >= HIT for q in qs), len(qs)),
                full_misses=sum(q < FULL_MISS for q in qs))


# ---------- direct API ----------
def analyze_api(rows):
    by_route = collections.defaultdict(list)
    for r in rows:
        by_route[r["route"]].append(r)
    summary, seqs, by_route_repeats = {}, {}, {}
    for route in ordered(by_route):
        all_rows = by_route[route]
        groups = collections.defaultdict(list)
        for r in all_rows:
            groups[(r["trial"], r["size"])].append(r)
        ok = [r for r in all_rows if r.get("status") == 200 and r.get("prompt")]
        valid_steps = {(r["trial"], r["size"], r["step"]): r for r in ok}
        scored = []  # dicts with size, label, reuse, seconds
        for key, seq in groups.items():
            seq.sort(key=lambda r: r["step"])
            marks = []
            for r in seq:
                step = r["step"]
                q = None
                if (*key, step) in valid_steps and r["label"] != "cold":
                    prev = valid_steps.get((*key, step - 1))
                    reusable = r["prompt"] if r["label"].startswith("repeat") else (prev or {}).get("prompt")
                    q = ratio(r["cached"], reusable)
                marks.append(q)
                if q is not None:
                    scored.append(dict(size=r["size"], label=r["label"], reuse=q, seconds=r["seconds"]))
            seqs[(route, *key)] = marks
        if not ok:
            summary[route] = dict(name=name(route), requests=len(all_rows), errors=len(all_rows),
                                  first_error=(all_rows[0].get("error") or "")[:300] if all_rows else None)
            continue
        repeats = [s for s in scored if s["label"].startswith("repeat")]
        by_route_repeats[route] = repeats
        latency_by_size = {}
        for k in SIZES:
            cold = [r["seconds"] for r in ok if r["size"] == k and r["label"] == "cold"]
            hit = [s["seconds"] for s in scored if s["size"] == k and s["reuse"] >= HIT]
            miss = [s["seconds"] for s in scored if s["size"] == k and s["reuse"] < HIT]
            if cold or hit or miss:
                latency_by_size[k] = dict(cold_median=med(cold), hit_median=med(hit), hits=len(hit), miss_median=med(miss), misses=len(miss))
        secs = sorted(r["seconds"] for r in ok)
        hit_secs = [s["seconds"] for s in repeats if s["reuse"] >= HIT]
        miss_secs = [s["seconds"] for s in repeats if s["reuse"] < HIT]
        summary[route] = dict(
            name=name(route), requests=len(all_rows), errors=len(all_rows) - len(ok),
            all_warm=hit_stats([s["reuse"] for s in scored]),
            repeats=hit_stats([s["reuse"] for s in repeats]),
            grow=hit_stats([s["reuse"] for s in scored if s["label"].startswith("grow")]),
            delayed=hit_stats([s["reuse"] for s in scored if s["label"].startswith("repeat-after")]),
            repeats_by_size={k: hit_stats([s["reuse"] for s in repeats if s["size"] == k]) for k in SIZES if any(s["size"] == k for s in repeats)},
            prompt_tokens_by_size={k: statistics.median(r["prompt"] for r in ok if r["size"] == k) for k in SIZES if any(r["size"] == k for r in ok)},
            hit_then_miss=sum(1 for (rt, *_), m in seqs.items() if rt == route
                              for a, b in zip(m, m[1:]) if a is not None and b is not None and a >= HIT and b < HIT),
            median_seconds=round(statistics.median(secs), 2), p90_seconds=round(secs[int(0.9 * (len(secs) - 1))], 2),
            max_seconds=secs[-1],
            repeat_hit_median_seconds=round(statistics.median(hit_secs), 1) if hit_secs else None, repeat_hits=len(hit_secs),
            repeat_miss_median_seconds=round(statistics.median(miss_secs), 1) if miss_secs else None, repeat_misses=len(miss_secs),
            latency_by_size=latency_by_size,
        )
    group_summary = {}
    groups = {"deploy_default_endpoints": [r for r in by_route_repeats if r.startswith("deploy") and "pref" not in r],
              "other_hosts": [r for r in by_route_repeats if not r.startswith("deploy")]}
    for g, routes in groups.items():
        reps = [s for r in routes for s in by_route_repeats[r]]
        group_summary[g] = dict(
            routes=routes,
            repeats_under_10k=hit_stats([s["reuse"] for s in reps if s["size"] in ("xs", "s")]),
            repeats_by_size={k: hit_stats([s["reuse"] for s in reps if s["size"] == k]) for k in SIZES if any(s["size"] == k for s in reps)})
    return summary, seqs, group_summary


# ---------- OpenCode sessions (CLI and T3) ----------
def analyze_sessions(rows):
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by[r["route"]][r["trial"]].append(r)
    summary, marks = {}, {}
    for route in ordered(by):
        sessions = []
        for trial in sorted(by[route]):
            seq, prev, steps = sorted(by[route][trial], key=lambda r: r["step"]), None, []
            for r in seq:
                prompt = r["input"] + r["cached"]
                steps.append(dict(prompt=prompt, cached=r["cached"], reuse=None if prev is None else ratio(r["cached"], prev)))
                prev = prompt
            sessions.append(steps)
        qs = [s["reuse"] for sess in sessions for s in sess if s["reuse"] is not None]
        total = sum(s["prompt"] for sess in sessions for s in sess)
        cached = sum(s["cached"] for sess in sessions for s in sess)
        summary[route] = dict(name=name(route), sessions=len(sessions), requests=sum(len(s) for s in sessions), **hit_stats(qs),
                              sessions_without_a_miss=sum(all(s["reuse"] >= HIT for s in sess if s["reuse"] is not None) for sess in sessions),
                              cached_share_pct=pct(cached, total),
                              session_minutes_median=med([(max(r["ts"] for r in by[route][t]) - min(r["ts"] for r in by[route][t])) / 60000
                                                          for t in by[route]]))
        marks[route] = [[s["reuse"] for s in sess] for sess in sessions]
    return summary, marks


# ---------- everyday sessions ----------
def analyze_everyday(rows, fresh_price, cached_price):
    def pos(p):
        return "2–5" if p <= 5 else "6–20" if p <= 20 else "21+"

    def size(p):
        return "under 16k" if p < 16000 else "16k–64k" if p < 64000 else "64k–128k" if p < 128000 else "128k+"

    eligible = [dict(r, reuse=ratio(r["cached"], r["prev_prompt"])) for r in rows if r["prev_prompt"] and r["prompt"] >= 0.9 * r["prev_prompt"]]

    def miss(items):
        return dict(n=len(items), miss_pct=pct(sum(r["reuse"] < HIT for r in items), len(items)),
                    full_miss_pct=pct(sum(r["reuse"] < FULL_MISS for r in items), len(items)))

    by_session = collections.defaultdict(list)
    for r in eligible:
        by_session[(r["host"], r["session"])].append(r)
    long_sessions = [v for v in by_session.values() if len(v) >= 10]
    cached = sum(r["cached"] for r in rows)
    total = sum(r["prompt"] for r in rows)
    def shortfall(r):
        return r["prev_prompt"] - min(r["cached"], r["prev_prompt"])

    lost = sum(shortfall(r) for r in eligible if r["reuse"] < HIT)
    lost_full = sum(shortfall(r) for r in eligible if r["reuse"] < FULL_MISS)
    misses = [r for r in eligible if r["reuse"] < HIT]
    signature = collections.Counter(r["cached"] for r in misses).most_common(4)
    previous_ts = {}
    for rs in by_session.values():
        rs.sort(key=lambda r: r["position"])
    for r in rows:
        previous_ts[(r["host"], r["session"], r["position"])] = r["ts"]
    gap = {id(r): (r["ts"] - previous_ts.get((r["host"], r["session"], r["position"] - 1), r["ts"])) / 1000 for r in eligible}
    lost_after_pause = sum(shortfall(r) for r in misses if gap[id(r)] > 300)
    input_spend = ((total - cached) * fresh_price + cached * cached_price) / 1e6
    return dict(
        requests=len(rows), sessions=len({(r["host"], r["session"]) for r in rows}), eligible=len(eligible),
        overall=miss(eligible),
        by_position={b: miss([r for r in eligible if pos(r["position"]) == b]) for b in ["2–5", "6–20", "21+"]},
        by_size={b: miss([r for r in eligible if size(r["prompt"]) == b]) for b in ["under 16k", "16k–64k", "64k–128k", "128k+"]},
        sessions_10plus=len(long_sessions),
        sessions_10plus_with_a_miss=sum(any(r["reuse"] < HIT for r in v) for v in long_sessions),
        cached_share_pct=pct(cached, total),
        fresh_tokens_total=total - cached,
        fresh_tokens_from_misses=lost,
        share_of_fresh_from_misses_pct=pct(lost, total - cached),
        cost_of_misses_upper_bound_usd=round(lost * (fresh_price - cached_price) / 1e6, 2),
        fresh_tokens_from_full_misses=lost_full,
        cost_of_full_misses_usd=round(lost_full * (fresh_price - cached_price) / 1e6, 2),
        input_spend_usd=round(input_spend, 2),
        misses_share_of_input_spend_pct=[pct(lost_full * (fresh_price - cached_price) / 1e6, input_spend),
                                         pct(lost * (fresh_price - cached_price) / 1e6, input_spend)],
        misses=len(misses),
        most_common_cached_values_on_misses=[dict(cached_tokens=v, count=n) for v, n in signature],
        share_of_lost_tokens_after_pause_over_5min_pct=pct(lost_after_pause, lost),
    )


# ---------- charts ----------
def svg(path, w, h, body):
    path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                    f'font-family="Helvetica, Arial, sans-serif">{body}</svg>')


def text(x, y, s, size=12, fill=INK, weight="normal"):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}">{s}</text>'


def square(x, y, size, q):
    c = PALE if q is None else GREEN if q >= 0.9 else AMBER if q >= HIT else RED
    return f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="2" fill="{c}"/>'


def legend(y):
    out, x = "", 0
    for c, label in [(GREEN, "reused 90%+"), (AMBER, "reused 50–90%"), (RED, "missed (under 50%)"), (PALE, "unscored")]:
        out += f'<rect x="{x}" y="{y - 10}" width="12" height="12" rx="2" fill="{c}"/>' + text(x + 17, y, label, 11, MUTED)
        x += 140
    return out


def session_chart(path, marks, title):
    cell, gap, left = 18, 3, 250
    body, y = text(0, 18, title, 14, weight="bold"), 40
    width = left + max(len(s) for m in marks.values() for s in m) * (cell + gap) + 20
    for route, sessions in marks.items():
        body += text(0, y + 13, name(route), 12, weight="bold")
        y += cell + gap
        for i, seq in enumerate(sessions):
            body += text(12, y + 13, f"run {i + 1}", 11, MUTED) + "".join(square(left + j * (cell + gap), y, cell, q) for j, q in enumerate(seq))
            y += cell + gap
    svg(path, max(width, 580), y + 40, body + legend(y + 22))


def api_chart(path, seqs, routes):
    cell, gap, left = 13, 2, 285
    block = 9 * (cell + gap) + 16
    runs = sorted({k[1] for k in seqs})
    body = text(0, 18, "Direct API: every call, one square each", 14, weight="bold")
    body += text(0, 36, "Each block: cold, 4 identical repeats, delayed repeat, 3 growing turns", 11, MUTED)
    y = 62
    body += "".join(text(left + i * block, y, label, 11, MUTED, "bold") for i, label in enumerate(SIZES.values()))
    y += 10
    for route in routes:
        body += text(0, y + 11, name(route), 12)
        for run in runs:
            body += text(left - 40, y + 11, f"run {run + 1}", 10, MUTED)
            for i, s in enumerate(SIZES):
                body += "".join(square(left + i * block + j * (cell + gap), y, cell, q) for j, q in enumerate(seqs.get((route, run, s), [])))
            y += cell + 3
        y += 8
    svg(path, left + 4 * block, y + 30, body + legend(y + 18))


def bar_chart(path, title, groups, fmt, color_for, max_value=100, ticks=None):
    left, bw, bh = 300, 300, 16
    body, y = text(0, 18, title, 14, weight="bold"), 36
    for group, items in groups:
        if group:
            y += 10
            body += text(0, y + 12, group, 12, MUTED, "bold")
            y += bh + 6
        for label, v in items:
            body += text(12, y + 12, label, 12) + f'<rect x="{left}" y="{y}" width="{bw}" height="{bh}" fill="{GRID}" rx="3"/>'
            body += f'<rect x="{left}" y="{y}" width="{bw * v / max_value:.1f}" height="{bh}" fill="{color_for(label)}" rx="3"/>'
            if ticks and label in ticks:
                x = left + bw * ticks[label] / max_value
                body += f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y - 2}" y2="{y + bh + 2}" stroke="{INK}" stroke-width="2"/>'
            body += text(left + bw + 8, y + 12, fmt(label, v), 12)
            y += bh + 6
    svg(path, 720, y + 10, body)


def deploy_red(label):
    return RED if label.startswith("Deploy") else GREEN


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", type=pathlib.Path, default=pathlib.Path("results/api"))
    p.add_argument("--opencode", type=pathlib.Path, default=pathlib.Path("results/opencode"))
    p.add_argument("--t3", type=pathlib.Path, default=pathlib.Path("results/t3"))
    p.add_argument("--sessions", type=pathlib.Path, default=pathlib.Path("results/sessions"))
    p.add_argument("--fresh-price", type=float, default=0.10, help="USD per million uncached input tokens")
    p.add_argument("--cached-price", type=float, default=0.003, help="USD per million cached input tokens")
    p.add_argument("--out", type=pathlib.Path, default=pathlib.Path("report"))
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    api_rows = load_jsonl(a.api)
    api, seqs, api_groups = analyze_api(api_rows)
    cli, cli_marks = analyze_sessions(load_jsonl(a.opencode))
    t3, t3_marks = analyze_sessions(load_jsonl(a.t3))
    everyday_rows = [r for f in sorted(a.sessions.glob("*.json")) for r in json.loads(f.read_text())] if a.sessions.is_dir() else []
    everyday = analyze_everyday(everyday_rows, a.fresh_price, a.cached_price) if everyday_rows else None
    result = dict(hit_threshold=HIT, full_miss_threshold=FULL_MISS, prices_usd_per_million=dict(fresh=a.fresh_price, cached=a.cached_price),
                  api=api, api_groups=api_groups, opencode_cli=cli, t3_opencode=t3, everyday=everyday)
    (a.out / "analysis.json").write_text(json.dumps(result, indent=1))

    if api_rows:
        with (a.out / "deploy-request-index.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["utc", "route", "run", "prompt_size", "step", "label", "prompt_tokens", "cached_tokens", "seconds", "x_request_id", "response_id"])
            for r in sorted(api_rows, key=lambda r: (ordered([r["route"]]), r["route"], r["trial"], list(SIZES).index(r["size"]), r["step"])):
                if r["route"].startswith("deploy") and r.get("status") == 200:
                    w.writerow([r["utc"], r["route"], r["trial"] + 1, r["size"], r["step"], r["label"], r.get("prompt"), r.get("cached"),
                                r["seconds"], r.get("request_id"), r.get("response_id")])

    working = [r for r in api if "all_warm" in api[r]]
    groups = []
    if working:
        api_chart(a.out / "api-sequences.svg", seqs, working)
        reuse = [(name(r), api[r]["all_warm"]["hit_pct"]) for r in working if api[r]["all_warm"]["n"]]
        if reuse:
            groups.append(("Direct API", reuse))
        medians = [(name(r), api[r]["median_seconds"]) for r in working]
        p90s = {name(r): api[r]["p90_seconds"] for r in working}
        scale = max(0.01, max(p90s.values()), max(v for _, v in medians))
        bar_chart(a.out / "latency.svg", "Direct API response time (median bar, 90th percentile tick), seconds",
                  [("", medians)], lambda label, v: f"{v:.1f}s / {p90s[label]:.1f}s", deploy_red, scale, p90s)
    if cli:
        session_chart(a.out / "cli-sessions.svg", cli_marks, "OpenCode CLI: each square is one request in a six-turn session")
        groups.append(("OpenCode CLI", [(name(r), s["hit_pct"]) for r, s in cli.items()]))
    if t3:
        session_chart(a.out / "t3-sessions.svg", t3_marks, "T3 → OpenCode: each square is one request in a six-step agent session")
        groups.append(("T3 → OpenCode", [(name(r), s["hit_pct"]) for r, s in t3.items()]))
    if groups:
        bar_chart(a.out / "hit-rates.svg", "Share of requests that reused the cache, by path and provider", groups,
                  lambda _l, v: f"{v:.0f}%", deploy_red)
    if everyday:
        bar_chart(a.out / "everyday.svg", "Everyday OpenCode sessions on Deploy: requests that lost the cache",
                  [("By position in session", [(f"{b} ({s['n']:,} requests)", s["miss_pct"] or 0) for b, s in everyday["by_position"].items()]),
                   ("By prompt size", [(f"{b} ({s['n']:,} requests)", s["miss_pct"] or 0) for b, s in everyday["by_size"].items()])],
                  lambda _l, v: f"{v:.1f}%", lambda _l: RED)

    print(f"Reused cache = at least {HIT:.0%} of the reusable prompt came back cached. Full miss = under {FULL_MISS:.0%}.\n")
    for r, s in api.items():
        if "all_warm" not in s:
            print(f"API  {s['name']:38} no successful requests: {s['first_error']}")
            continue
        if not s["all_warm"]["n"]:
            print(f"API  {s['name']:38} no requests with scorable cache reuse")
            continue
        print(f"API  {s['name']:38} reused {s['all_warm']['hit_pct']:5}%  full misses {s['all_warm']['full_misses']:3}/{s['all_warm']['n']}"
              f"  median {s['median_seconds']}s  p90 {s['p90_seconds']}s")
    for label, block in [("CLI", cli), ("T3 ", t3)]:
        for r, s in block.items():
            print(f"{label}  {s['name']:38} reused {s['hit_pct']:5}%  full misses {s['full_misses']:3}/{s['n']}"
                  f"  sessions without a miss {s['sessions_without_a_miss']}/{s['sessions']}")
    if everyday:
        print(f"Everyday: {everyday['eligible']:,} requests, {everyday['overall']['miss_pct']}% lost the cache; "
              f"{everyday['fresh_tokens_from_misses']:,} full-price tokens from misses (upper bound ${everyday['cost_of_misses_upper_bound_usd']})")
    print(f"\nWrote {a.out}/")


if __name__ == "__main__":
    main()
