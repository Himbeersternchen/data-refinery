"""Eurostat's open dissemination API (JSON-stat 2.0).
Eurostat 开放数据接口。数据按 CC BY 4.0 授权，官方允许程序访问，不需要密钥。

    https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access

Metric config in the evidence file / 证据表里这样写：

    fetch:
      provider: eurostat
      dataset: nrg_ind_ren
      params: {nrg_bal: REN, unit: PC, time: "2024"}
      key: eurostat          # 用对象 keys 里的哪个字段当国家代码（geo）
"""

from __future__ import annotations

import requests

URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
HEADERS = {"Accept": "application/json", "User-Agent": "data-refinery/0.1"}


def parse(doc: dict) -> dict[str, float]:
    """JSON-stat 是一个按维度展平的数组：位置 = 各维度下标按 size 进位相加。
    这里只认一个变化的维度 geo，其他维度都应该已经被 params 固定成 1 个值。"""
    ids, sizes, dims = doc["id"], doc["size"], doc["dimension"]
    for d, n in zip(ids, sizes):
        if d != "geo" and n != 1:
            raise ValueError(f"dimension {d} has {n} values; pin it to one value in params")
    geo_index = dims["geo"]["category"]["index"]
    stride = 1
    for d, n in zip(reversed(ids), reversed(sizes)):
        if d == "geo":
            break
        stride *= n
    values = doc.get("value", {})
    out = {}
    for geo, pos in geo_index.items():
        v = values.get(str(pos * stride)) if isinstance(values, dict) else values[pos * stride]
        if v is not None:
            out[geo] = float(v)
    return out


def fetch(dataset: str, params: dict, geos: list[str], get=requests.get) -> dict[str, float]:
    query = [(k, v) for k, v in params.items()] + [("geo", g) for g in geos] + [("format", "JSON")]
    r = get(URL.format(dataset=dataset), params=query, headers=HEADERS, timeout=60)
    if r.status_code != 200:
        raise ValueError(f"Eurostat {dataset}: HTTP {r.status_code}: {' '.join(r.text.split())[:200]}")
    return parse(r.json())
