"""Command line / 命令行

    python -m refinery check  examples/renewables-eu/evidence.yaml
    python -m refinery build  examples/renewables-eu/evidence.yaml            # CSV, offline
    python -m refinery build  examples/renewables-eu/evidence.yaml --html     # web page
    python -m refinery build  examples/renewables-eu/evidence.yaml --live     # refresh metrics from open APIs first
    python -m refinery build  examples/job-shortage-de/evidence.yaml --sort score

Output goes to out/<example>_<date>.csv|html unless --out is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

from refinery import evidence, fetch, output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m refinery", description="Rank things by public evidence.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate an evidence file / 只校验证据表")
    c.add_argument("evidence")
    b = sub.add_parser("build", help="rank and write CSV or HTML / 排序并输出")
    b.add_argument("evidence")
    b.add_argument("--sort", help="score or a metric id (default: default_sort in the file)")
    b.add_argument("--live", action="store_true", help="fetch metrics from open APIs and save a snapshot first")
    b.add_argument("--html", action="store_true", help="write a web page instead of CSV")
    b.add_argument("--out", help="output file (default: out/<folder>_<date>.csv|html)")
    args = ap.parse_args(argv)

    try:
        ev = evidence.load(args.evidence)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"① {ev.path}: {len(ev.items)} items, {len(ev.sources)} sources, {len(ev.metrics)} metrics")
    if args.cmd == "check":
        print("OK")
        return 0

    if args.live:
        print("② fetching metrics from open APIs")
        try:
            for mid, n in fetch.refresh(ev).items():
                print(f"   {mid}: {n} / {len(ev.items)}")
        except (requests.RequestException, ValueError) as e:
            print(f"fetch failed, snapshot left unchanged: {e}", file=sys.stderr)
            return 3
        ev = evidence.load(args.evidence)          # 重新读一遍，把新快照合进来
    fetched_at = None
    if ev.snapshot_path.exists():
        fetched_at = json.loads(ev.snapshot_path.read_text(encoding="utf-8")).get("fetched_at")

    by = args.sort or ev.default_sort
    try:
        ranked = evidence.rank(ev, by)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    today = date.today().isoformat()
    out = Path(args.out or f"out/{ev.path.parent.name}_{today}.{'html' if args.html else 'csv'}")
    try:
        if args.html:
            output.write_html(ev, ranked, by, today, fetched_at, out)
            extra = ""
        else:
            extra = f" (column guide: {output.write_csv(ev, ranked, by, out)})"
    except (ValueError, OSError) as e:
        print(e, file=sys.stderr)
        return 1

    print(f"③ sorted by {by}")
    shown = ev.shown_metrics(by)
    for i, it in enumerate(ranked[:10], 1):
        s, up, down = evidence.score(it)
        ms = "  ".join(f"{mid}={output._fmt(it['values'].get(mid, {}).get('value')) or '–'}" for mid in shown)
        print(f"  {i:>2}. {it['name']:<28} score {s:+d} (↑{up} ↓{down})  {ms}")
    if len(ranked) > 10:
        print(f"  … {len(ranked) - 10} more")
    print(f"→ {out}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
