"""Offline tests: no network. Run with `python tests/test_offline.py` or pytest.
离线测试，不联网。"""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from refinery import evidence, fetch, output
from refinery.fetch import eurostat


def test_examples_load_and_rank():
    for name in ("job-shortage-de", "renewables-eu"):
        ev = evidence.load(ROOT / "examples" / name / "evidence.yaml")
        ranked = evidence.rank(ev)
        assert len(ranked) == len(ev.items) > 10
    jobs = evidence.load(ROOT / "examples/job-shortage-de/evidence.yaml")
    scores = [evidence.score(it)[0] for it in evidence.rank(jobs, "score")]
    assert scores == sorted(scores, reverse=True)


def test_snapshot_and_derived_metric():
    ev = evidence.load(ROOT / "examples/renewables-eu/evidence.yaml")
    dk = next(it for it in ev.items if it["id"] == "DK")["values"]
    assert dk["share_2024"]["origin"] == "snapshot" and dk["change_10y"]["origin"] == "derived"
    assert abs(dk["change_10y"]["value"] - (dk["share_2024"]["value"] - dk["share_2014"]["value"])) < 1e-6
    changes = [it["values"]["change_10y"]["value"] for it in evidence.rank(ev, "change_10y")]
    assert changes == sorted(changes, reverse=True)


def test_validation_lists_every_problem():
    tmp = Path(tempfile.mkdtemp())
    try:
        bad = tmp / "evidence.yaml"
        bad.write_text("""
lang: fr
sources: {a: {name: A, url: u, tier: rumour}}
metrics: {m: {label: M, derive: {op: add, a: m, b: x}}}
items:
  - {id: x, name: X, signals: [{source: b, trend: sideways}], metrics: {m: "high", n: 1}}
  - {id: x, name: Y}
""", encoding="utf-8")
        try:
            evidence.load(bad)
            raise AssertionError("should fail")
        except ValueError as e:
            msg = str(e)
        for word in ("lang", "tier", "derive", "source 'b'", "trend", "claim", "must be a number",
                     "metric n", "duplicate"):
            assert word in msg, (word, msg)
    finally:
        shutil.rmtree(tmp)


def test_eurostat_parse_and_refresh():
    # 两个国家，geo 在 time 前面：位置 = geo下标 × time的size + time下标
    doc = {"id": ["freq", "unit", "geo", "time"], "size": [1, 1, 2, 1],
           "dimension": {"geo": {"category": {"index": {"DK": 0, "SE": 1}}}},
           "value": {"0": 46.5, "1": 62.8}}
    assert eurostat.parse(doc) == {"DK": 46.5, "SE": 62.8}
    try:
        eurostat.parse(dict(doc, size=[1, 1, 2, 2]))
        raise AssertionError("two time values should be rejected")
    except ValueError as e:
        assert "time" in str(e)

    tmp = Path(tempfile.mkdtemp())
    try:
        src = ROOT / "examples/renewables-eu/evidence.yaml"
        shutil.copy(src, tmp / "evidence.yaml")
        ev = evidence.load(tmp / "evidence.yaml")
        real = fetch.PROVIDERS["eurostat"]
        fetch.PROVIDERS["eurostat"] = lambda dataset, params, geos: {g: 10.0 if params["time"] == "2014" else 30.0
                                                                      for g in geos if g != "MT"}
        try:
            filled = fetch.refresh(ev)
        finally:
            fetch.PROVIDERS["eurostat"] = real
        assert filled == {"share_2014": 26, "share_2024": 26}
        ev = evidence.load(tmp / "evidence.yaml")
        assert ev.items[0]["values"]["change_10y"]["value"] == 20.0
        mt = next(it for it in ev.items if it["id"] == "MT")
        assert "share_2024" not in mt["values"] and evidence.rank(ev, "change_10y")[-1]["id"] == "MT"
        assert json.loads(ev.snapshot_path.read_text())["fetched_at"]
    finally:
        shutil.rmtree(tmp)


def test_outputs():
    ev = evidence.load(ROOT / "examples/job-shortage-de/evidence.yaml")
    ranked = evidence.rank(ev, "salary_year")
    tmp = Path(tempfile.mkdtemp())
    try:
        out = tmp / "r.csv"
        guide = output.write_csv(ev, ranked, "salary_year", out)
        with out.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(ev.items) and rows[0]["年薪中位数 (€)"] == "95064"   # 原始数字，Excel 能计算
        assert rows[0]["来源链接"].startswith("https://")
        with guide.open(encoding="utf-8-sig") as f:
            assert [r[0] for r in csv.reader(f)][1:] == list(rows[0])
    finally:
        shutil.rmtree(tmp)

    ev.items[0]["signals"].append({"source": "kofa-2025", "trend": "mixed", "claim": "<script>x</script>"})
    page = output.render_html(ev, ranked, "salary_year", "2026-11-02", None)
    assert '<details class="colhelp">' in page and '<details class="refs">' in page
    assert "<details class=\"colhelp\" open" not in page
    assert page.count("<tr><td") == len(ev.items)
    assert "&lt;script&gt;" in page and "<script>x" not in page
    assert "↑ 紧缺" in page
    assert "未联网" not in page, "没有 fetch 配置的证据表不该提示 --live"
    assert "第三方，非 BA 官网" in page, "指标的 note 要出现在网页里"

    assert output._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and output._cell(-3) == -3
    ev.sources["kofa-2025"]["url"] = "javascript:alert(1)"
    assert 'href="javascript' not in output.render_html(ev, ranked, "salary_year", "2026-11-02", None)


def test_more_validation_and_cli():
    tmp = Path(tempfile.mkdtemp())
    try:
        bad = tmp / "evidence.yaml"
        bad.write_text("""
sources: {a: {name: A, url: u, tier: official}}
metrics:
  m: {label: M, fetch: {provider: worldbank}}
  n: {label: N}
items:
  - {id: x, name: X, metrics: {n: true}}
""", encoding="utf-8")
        msg = ""
        try:
            evidence.load(bad)
        except ValueError as e:
            msg = str(e)
        assert "fetch needs provider" in msg and "must be a number, got True" in msg, msg

        for text, word in [("sources: [1, 2]", "sources must be a mapping"),
                           ("items: [{id: x, name: X, signals: [hello]}]", "each signal must be a mapping"),
                           ("a: [unclosed", "not valid YAML")]:
            bad.write_text(text, encoding="utf-8")
            try:
                evidence.load(bad)
                raise AssertionError(word)
            except ValueError as e:
                assert word in str(e), (word, str(e))
        try:
            evidence.load(tmp / "missing.yaml")
            raise AssertionError("missing file")
        except ValueError as e:
            assert "cannot read" in str(e)

        # 派生指标可以依赖后面定义的派生指标
        bad.write_text("""
metrics:
  d2: {label: D2, derive: {op: subtract, a: d1, b: c}}
  d1: {label: D1, derive: {op: subtract, a: a, b: b}}
  a: {label: A}
  b: {label: B}
  c: {label: C, better: lower}
items:
  - {id: x, name: X, metrics: {a: 10, b: 3, c: 1}}
  - {id: y, name: Y, metrics: {a: 10, b: 3, c: 5}}
""", encoding="utf-8")
        ev = evidence.load(bad)
        assert ev.items[0]["values"]["d2"]["value"] == 6
        assert [it["id"] for it in evidence.rank(ev, "c")] == ["x", "y"], "better: lower 越小越靠前"

        # 两列同名时报错，而不是让 CSV 错位
        bad.write_text("metrics: {a: {label: Score}}\nitems: [{id: x, name: X}]", encoding="utf-8")
        ev = evidence.load(bad)
        from refinery.__main__ import main
        assert main(["build", str(bad), "--out", str(tmp / "o.csv")]) == 1
        assert main(["build", str(bad), "--sort", "nope", "--out", str(tmp / "o.csv")]) == 2
        assert main(["check", str(tmp / "missing.yaml")]) == 1

        # 按隐藏指标排序时，那一列要出现
        ren = evidence.load(ROOT / "examples/renewables-eu/evidence.yaml")
        assert "Share 2014 (%)" in output.columns(ren, "share_2014")
        assert "Share 2014 (%)" not in output.columns(ren, "change_10y")
    finally:
        shutil.rmtree(tmp)
    assert set(fetch.PROVIDERS) == set(evidence.FETCH_PROVIDERS)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
