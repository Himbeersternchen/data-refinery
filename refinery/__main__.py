"""Command line / 命令行

    python -m refinery check  examples/renewables-eu/evidence.yaml
    python -m refinery build  examples/renewables-eu/evidence.yaml            # CSV, offline
    python -m refinery build  examples/renewables-eu/evidence.yaml --html     # web page
    python -m refinery build  examples/renewables-eu/evidence.yaml --live     # refresh metrics from open APIs first
    python -m refinery build  examples/job-shortage-de/evidence.yaml --sort score
    python -m refinery extract examples/job-shortage-de/evidence.yaml --url <report>   # AI proposes signals into a draft
    python -m refinery extract my-question/evidence.yaml --question "…" --file report.pdf --source-url <link>
    python -m refinery merge   examples/job-shortage-de/evidence.yaml --accept-rest    # after marking the wrong ones reject

Output goes to out/<example>_<date>.csv|html unless --out is given.
extract writes evidence.proposed.yaml next to the evidence file and never touches the evidence file itself.
Only merge writes to the evidence file.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import requests

from refinery import evidence, extract, fetch, merge, output, report


def _extract(args: argparse.Namespace) -> int:
    try:
        rep = report.read_url(args.url, refresh=args.refresh) if args.url else report.read_file(args.file)
        print(f"① {args.url or args.file}: {len(rep.text):,} characters of text")
        print(f"② asking {args.model}; a long report can take several minutes")
        got = extract.propose(args.evidence, args.question, rep, lang=args.lang, source_url=args.source_url,
                              model=args.model)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    c = got["checks"]
    print(f"③ {got['added']} proposals from source {got['source']}: "
          f"{c['found']} quotes found, {c['not_found']} not found, {c['too_short']} too short")
    u = got["usage"]
    if u:
        total_in = sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        print(f"   tokens: {total_in:,} in, {u.get('output_tokens', 0):,} out")
    print(f"→ {got['path']}  review it: mark the wrong ones decision: reject, then run merge --accept-rest")
    return 0


def _merge(args: argparse.Namespace) -> int:
    try:
        got = merge.merge(args.evidence, accept_rest=args.accept_rest)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"merged {got['merged']} signals ({got['new_items']} new items), rejected {got['rejected']}, "
          f"{got['kept']} still in the draft")
    if got["kept"]:
        print(f"→ {extract.draft_path(args.evidence)}  the rest need your decision")
    if got["merged"]:
        print(f"→ {got['path']}{' (new file)' if got['created'] else ''}  next: python -m refinery build {args.evidence} --html")
    return 0


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
    e = sub.add_parser("extract", help="AI proposes signals from one report into a draft / AI 从一份报告提出信号，写进草稿")
    e.add_argument("evidence", help="evidence file the draft is for; it may not exist yet")
    e.add_argument("--question", help="default: the question in the evidence file")
    where = e.add_mutually_exclusive_group(required=True)
    where.add_argument("--url", help="link to the report (PDF or web page)")
    where.add_argument("--file", help="local copy of the report (PDF, HTML or text)")
    e.add_argument("--source-url", help="public link to cite, when the report was read from --file")
    e.add_argument("--refresh", action="store_true", help="download again instead of using the cached text")
    e.add_argument("--lang", choices=("en", "zh"), help="language of the claims, for a new evidence file (default: en)")
    e.add_argument("--model", default=extract.DEFAULT_MODEL, help="default: %(default)s")
    m = sub.add_parser("merge", help="write accepted proposals into the evidence file / 把接受的条目写进证据表")
    m.add_argument("evidence", help="evidence file; the draft next to it is merged")
    m.add_argument("--accept-rest", action="store_true", help="also accept every pending proposal the program has not flagged")
    args = ap.parse_args(argv)
    if args.cmd == "extract":
        return _extract(args)
    if args.cmd == "merge":
        return _merge(args)

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
