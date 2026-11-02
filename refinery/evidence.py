"""Load, validate and score an evidence file.
读取、校验证据表，并给每个对象打分。

An evidence file answers one research question. It lists
  sources  where the information comes from, each with a trust tier
  metrics  numeric indicators (typed in by hand, or fetched from an open API with --live)
  items    the things being ranked; each carries signals (one source's verdict: up / down / mixed)
           and metric values

一份证据表回答一个研究问题。score = 「up」信号数 − 「down」信号数，mixed 不计分。
各来源口径不同，数字之间不能直接比，所以只数「几个来源站哪边」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

TRENDS = {"up": 1, "down": -1, "mixed": 0}
TIERS = ("official", "research", "industry", "media", "secondary")
DERIVE_OPS = {"subtract": lambda a, b: a - b}
FETCH_PROVIDERS = ("eurostat",)          # 和 refinery/fetch/__init__.py 的 PROVIDERS 保持一致


@dataclass
class Evidence:
    path: Path
    title: str
    question: str
    lang: str
    default_sort: str
    trend_labels: dict
    sources: dict
    metrics: dict
    items: list

    @property
    def snapshot_path(self) -> Path:
        """--live 抓到的指标存在证据表旁边：evidence.yaml → evidence.metrics.json"""
        return self.path.with_name(self.path.stem + ".metrics.json")

    def shown_metrics(self, by: str | None = None) -> list[str]:
        """要显示的指标；按一个隐藏指标排序时，把它也显示出来。"""
        shown = [k for k, m in self.metrics.items() if m.get("show", True)]
        if by in self.metrics and by not in shown:
            shown.append(by)
        return shown

    def has_fetch(self) -> bool:
        return any(m.get("fetch") for m in self.metrics.values())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _problems(path: Path, errors: list[str]) -> ValueError:
    return ValueError(f"{path} has {len(errors)} problem(s):\n  - " + "\n  - ".join(errors))


def load(path: str | Path) -> Evidence:
    """出任何问题都抛 ValueError，信息里列出全部问题。"""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e.strerror or e}") from None
    except yaml.YAMLError as e:
        raise ValueError(f"{path} is not valid YAML:\n{e}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a mapping with sources / metrics / items")

    errors: list[str] = []
    sources = data.get("sources") or {}
    metrics = data.get("metrics") or {}
    items = data.get("items") or []
    for name, value, kind in (("sources", sources, dict), ("metrics", metrics, dict), ("items", items, list)):
        if not isinstance(value, kind):
            errors.append(f"{name} must be a {'mapping' if kind is dict else 'list'}")
    if errors:
        raise _problems(path, errors)

    lang = data.get("lang", "en")
    if lang not in ("en", "zh"):
        errors.append(f"lang must be en or zh, got {lang!r}")

    for sid, s in sources.items():
        if not isinstance(s, dict):
            errors.append(f"source {sid}: must be a mapping with name, url, tier")
            continue
        for k in ("name", "url"):
            if not s.get(k):
                errors.append(f"source {sid}: missing {k}")
        if s.get("tier") not in TIERS:
            errors.append(f"source {sid}: tier must be one of {', '.join(TIERS)}, got {s.get('tier')!r}")

    for mid, m in metrics.items():
        if not isinstance(m, dict):
            errors.append(f"metric {mid}: must be a mapping with at least a label")
            continue
        if not m.get("label"):
            errors.append(f"metric {mid}: missing label")
        if m.get("source") and m["source"] not in sources:
            errors.append(f"metric {mid}: source {m['source']} is not listed under sources")
        if m.get("better", "higher") not in ("higher", "lower"):
            errors.append(f"metric {mid}: better must be higher or lower")
        d = m.get("derive")
        if d is not None and (not isinstance(d, dict) or d.get("op") not in DERIVE_OPS
                              or d.get("a") not in metrics or d.get("b") not in metrics):
            errors.append(f"metric {mid}: derive needs op ({', '.join(DERIVE_OPS)}) and two existing metrics a, b")
        f = m.get("fetch")
        if f is not None and (not isinstance(f, dict) or f.get("provider") not in FETCH_PROVIDERS
                              or not f.get("dataset")):
            errors.append(f"metric {mid}: fetch needs provider ({', '.join(FETCH_PROVIDERS)}) and dataset")

    seen = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.append(f"item #{i + 1}: must be a mapping with id and name")
            continue
        iid = it.get("id") or f"#{i + 1}"
        if not it.get("id") or not it.get("name"):
            errors.append(f"item {iid}: needs id and name")
        if iid in seen:
            errors.append(f"item {iid}: duplicate id")
        seen.add(iid)
        sigs = it.get("signals") or []
        if not isinstance(sigs, list):
            errors.append(f"item {iid}: signals must be a list")
            sigs = []
        for sig in sigs:
            if not isinstance(sig, dict):
                errors.append(f"item {iid}: each signal must be a mapping with source, trend, claim")
                continue
            if sig.get("source") not in sources:
                errors.append(f"item {iid}: signal source {sig.get('source')!r} is not listed under sources")
            if sig.get("trend") not in TRENDS:
                errors.append(f"item {iid}: trend must be up / down / mixed, got {sig.get('trend')!r}")
            if not sig.get("claim"):
                errors.append(f"item {iid}: every signal needs a claim (what the source says)")
        vals = it.get("metrics") or {}
        if not isinstance(vals, dict):
            errors.append(f"item {iid}: metrics must be a mapping")
            vals = {}
        for mid, v in vals.items():
            if mid not in metrics:
                errors.append(f"item {iid}: metric {mid} is not defined under metrics")
            val = v.get("value") if isinstance(v, dict) else v
            if not _is_number(val):
                errors.append(f"item {iid}: metric {mid} must be a number, got {val!r}")

    sort = data.get("default_sort", "score")
    if sort != "score" and sort not in metrics:
        errors.append(f"default_sort must be score or a metric id, got {sort!r}")

    if errors:
        raise _problems(path, errors)

    ev = Evidence(path=path, title=data.get("title", path.stem), question=data.get("question", ""),
                  lang=lang, default_sort=sort, trend_labels=data.get("trend_labels") or {},
                  sources=sources, metrics=metrics, items=items)
    _fill_values(ev)
    return ev


def _fill_values(ev: Evidence) -> None:
    """每个对象的 values[metric] = {value, url?, note?, origin}。
    优先用表里手写的，其次用 --live 的快照，最后算派生指标（派生可以依赖派生，算到不再变为止）。"""
    snap = {}
    if ev.snapshot_path.exists():
        try:
            snap = json.loads(ev.snapshot_path.read_text(encoding="utf-8")).get("values", {})
        except (OSError, ValueError) as e:
            raise ValueError(f"cannot read snapshot {ev.snapshot_path}: {e}") from None
    derived = {mid: m["derive"] for mid, m in ev.metrics.items() if m.get("derive")}
    for it in ev.items:
        vals = {}
        for mid, v in (it.get("metrics") or {}).items():
            vals[mid] = dict(v, origin="file") if isinstance(v, dict) else {"value": v, "origin": "file"}
        for mid, by_item in snap.items():
            if mid in ev.metrics and mid not in vals and it["id"] in by_item:
                vals[mid] = {"value": by_item[it["id"]], "origin": "snapshot"}
        changed = True
        while changed:
            changed = False
            for mid, d in derived.items():
                if mid not in vals and d["a"] in vals and d["b"] in vals:
                    vals[mid] = {"value": round(DERIVE_OPS[d["op"]](vals[d["a"]]["value"], vals[d["b"]]["value"]), 6),
                                 "origin": "derived"}
                    changed = True
        it["values"] = vals


def score(item: dict) -> tuple[int, int, int]:
    """(score, up, down)"""
    sigs = item.get("signals") or []
    up = sum(1 for s in sigs if s["trend"] == "up")
    down = sum(1 for s in sigs if s["trend"] == "down")
    return up - down, up, down


def _metric_key(ev: Evidence, it: dict, mid: str) -> tuple:
    """(有没有值, 带方向的值)：better: lower 的指标取负，越小越靠前；没有值的排最后。"""
    v = it["values"].get(mid, {}).get("value")
    sign = -1 if ev.metrics[mid].get("better") == "lower" else 1
    return (v is not None, sign * v if v is not None else 0)


def rank(ev: Evidence, by: str | None = None) -> list[dict]:
    """by = "score" 或某个指标 id。"""
    by = by or ev.default_sort
    if by == "score":
        shown = ev.shown_metrics()
        tie = shown[0] if shown else None
        return sorted(ev.items, key=lambda it: (score(it)[0], *(_metric_key(ev, it, tie) if tie else ())),
                      reverse=True)
    if by not in ev.metrics:
        raise ValueError(f"cannot sort by {by!r}: choose score or one of {', '.join(ev.metrics)}")
    return sorted(ev.items, key=lambda it: (*_metric_key(ev, it, by), score(it)[0]), reverse=True)
