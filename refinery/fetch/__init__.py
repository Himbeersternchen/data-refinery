"""Fetch metric values from open data APIs and save them as a snapshot next to the evidence file.
从开放数据接口取指标，存成证据表旁边的快照文件（evidence.metrics.json），没联网也能出报告。

Only APIs whose terms allow automated access belong here. Never add scrapers for sites that forbid it.
只接条款允许程序访问的开放接口；禁止自动访问的网站一律不接。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from refinery.evidence import Evidence
from refinery.fetch import eurostat

PROVIDERS = {"eurostat": eurostat.fetch}


def refresh(ev: Evidence) -> dict[str, int]:
    """Fetch every metric that has a `fetch` block. Returns {metric: number of items filled}."""
    values, filled = {}, {}
    for mid, m in ev.metrics.items():
        f = m.get("fetch")
        if not f:
            continue
        if f["provider"] not in PROVIDERS:
            raise ValueError(f"metric {mid}: unknown provider {f['provider']!r} (known: {', '.join(PROVIDERS)})")
        key = f.get("key", f["provider"])
        code_to_item = {it["keys"][key]: it["id"] for it in ev.items if key in (it.get("keys") or {})}
        got = PROVIDERS[f["provider"]](f["dataset"], f.get("params", {}), list(code_to_item))
        values[mid] = {code_to_item[c]: v for c, v in got.items() if c in code_to_item}
        filled[mid] = len(values[mid])
    ev.snapshot_path.write_text(json.dumps(
        {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "values": values},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return filled
