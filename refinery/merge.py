"""Merge the accepted proposals of a draft into the evidence file.
把草稿里接受的条目合并进证据表。这是唯一会写证据表的 AI 相关命令，第 3 步的 agent 不该拿到它的权限。

    accept             写进证据表
    reject             丢弃
    pending            留在草稿里，下次再看
    accept_unverified  引用没查到原文，但人核对过，仍然写进证据表

--accept-rest accepts every pending proposal the program has not flagged, so a person only has to mark the wrong
ones. Flagged proposals (quote not found, numbers missing from the quote) always need a person's own decision.
--accept-rest 把程序没标记问题的 pending 全部接受，人只需要挑出错的。被标记的条目不会被自动接受。

The result is validated before anything is written: if there is any problem, neither file changes.
写入前先校验合并结果：只要有问题，证据表和草稿都不变。
"""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from refinery import evidence
from refinery.extract import HEADER, draft_path

DECISIONS = ("pending", "accept", "reject", "accept_unverified")


def _round_trip(raw: str = "") -> YAML:
    """PyYAML 读写一遍会丢掉注释，这里用 ruamel.yaml。列表缩进跟着文件原来的写法，合并后的 diff 里只有新增的内容。"""
    y = YAML()
    y.preserve_quotes = True
    y.width = 100000
    offset = 2 if re.search(r"^items:\s*\n  - ", raw, re.M) else 0
    y.indent(mapping=2, sequence=offset + 2, offset=offset)
    return y


def clean_quote(quote: str) -> str:
    """去掉 PDF 留下的换行和行尾断词：Hu-\\nmanmedizin → Humanmedizin，Hans-\\nBöckler → Hans-Böckler，Bau-\\nund 不连。"""
    quote = re.sub(r"(\w)-\s*\n\s*(?=[a-zäöüß])(?!und\b|oder\b|bis\b)", r"\1", quote)
    quote = re.sub(r"(\w)-\s*\n\s*(?=[A-ZÄÖÜ])", r"\1-", quote)
    return " ".join(quote.split())


def _is_flagged(p: dict) -> bool:
    return p.get("quote_check") != "found" or bool(p.get("claim_numbers_missing"))


def _decide(draft: dict, path: Path, accept_rest: bool) -> tuple[list[dict], list[dict], list[dict]]:
    """把条目分成 (写入, 丢弃, 留下)。一次列出所有问题。"""
    errors, accepted, rejected, kept = [], [], [], []
    for i, p in enumerate(draft.get("proposals") or [], 1):
        label = f"proposal #{i} ({p.get('item')})"
        d = p.get("decision")
        if d not in DECISIONS:
            errors.append(f"{label}: decision must be one of {', '.join(DECISIONS)}, got {d!r}")
        elif d == "accept" and p.get("quote_check") != "found":
            errors.append(f"{label}: quote_check is {p.get('quote_check')}. Check the original report, "
                          "then use accept_unverified if the quote is right")
        elif d in ("accept", "accept_unverified"):
            accepted.append(p)
        elif d == "reject":
            rejected.append(p)
        elif accept_rest and not _is_flagged(p):
            accepted.append(p)
        else:
            kept.append(p)
    if errors:
        raise ValueError(f"{path} has {len(errors)} problem(s):\n  - " + "\n  - ".join(errors))
    return accepted, rejected, kept


def _apply(data: dict, draft: dict, accepted: list[dict]) -> int:
    """把接受的条目写进 data，返回新增对象的数量。只登记用得上的来源。"""
    for key in ("sources", "items"):
        if not data.get(key):
            data[key] = CommentedSeq() if key == "items" else CommentedMap()
    items = {it["id"]: it for it in data["items"]}
    new_items = 0
    for p in accepted:
        sid = p.get("source")
        if sid not in data["sources"] and sid in (draft.get("sources") or {}):
            data["sources"][sid] = CommentedMap(draft["sources"][sid])
        item = items.get(p.get("item"))
        if item is None:
            item = CommentedMap(id=p.get("item"), name=p.get("name"))
            data["items"].append(item)
            items[item["id"]] = item
            new_items += 1
        if not item.get("signals"):
            item["signals"] = CommentedSeq()
        signal = CommentedMap(source=sid, trend=p.get("trend"), claim=p.get("claim"))
        if p.get("quote"):
            signal["quote"] = clean_quote(p["quote"])
        item["signals"].append(signal)
    return new_items


def _write_checked(path: Path, y: YAML, data: dict) -> None:
    """先写到旁边的临时文件并校验，通过了才替换，这样证据表不会停在写坏的状态。"""
    buf = io.StringIO()
    y.dump(data, buf)
    tmp = path.with_name(f".{path.stem}.merging.yaml")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    try:
        evidence.load(tmp)
    except ValueError as e:
        tmp.unlink()
        raise ValueError(str(e).replace(str(tmp), str(path))) from None
    os.replace(tmp, path)


def merge(evidence_path: str | Path, accept_rest: bool = False) -> dict:
    """返回 {merged, new_items, rejected, kept, created, path}。"""
    path = Path(evidence_path)
    dpath = draft_path(path)
    try:
        draft = yaml.safe_load(dpath.read_text(encoding="utf-8"))
    except OSError:
        raise ValueError(f"no draft to merge: {dpath} does not exist; run extract first") from None
    except yaml.YAMLError as e:
        raise ValueError(f"{dpath} is not valid YAML:\n{e}") from None
    if not isinstance(draft, dict):
        raise ValueError(f"{dpath}: the top level must be a mapping with sources / proposals")
    accepted, rejected, kept = _decide(draft, dpath, accept_rest)

    created = not path.exists()
    new_items = 0
    if accepted:
        if created:
            raw = ""
            data = CommentedMap(title=draft.get("question"), question=draft.get("question"), lang=draft.get("lang", "en"),
                                trend_labels=draft.get("trend_labels") or {})
        else:
            evidence.load(path)        # 先用自己的校验拦下有问题的证据表
            raw = path.read_text(encoding="utf-8")
            data = _round_trip(raw).load(raw)
        new_items = _apply(data, draft, accepted)
        _write_checked(path, _round_trip(raw), data)

    if kept:
        used = {p.get("source") for p in kept}
        draft["sources"] = {sid: s for sid, s in (draft.get("sources") or {}).items() if sid in used}
        draft["proposals"] = kept
        dpath.write_text(HEADER + yaml.safe_dump(draft, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
    else:
        dpath.unlink()                 # 都处理完了，草稿就没用了；写进去的内容可以用 git diff 查
    return {"merged": len(accepted), "new_items": new_items, "rejected": len(rejected), "kept": len(kept),
            "created": created and bool(accepted), "path": path}
