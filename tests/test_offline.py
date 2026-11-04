"""Offline tests: no network. Run with `python tests/test_offline.py` or pytest.
离线测试，不联网。"""
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from refinery import evidence, extract, fetch, merge, output, report
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
        fetch.PROVIDERS["eurostat"] = lambda dataset, params, geos, get=None: {g: 10.0 if params["time"] == "2014" else 30.0
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


def _pdf_with_text(text: str) -> bytes:
    """最小的单页 PDF。pypdf 写不出带文字的页面，所以手写，xref 表记录每个对象的字节位置。"""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<</Type /Catalog /Pages 2 0 R>>",
               b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
               b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources <</Font <</F1 5 0 R>>>>>>",
               b"<</Length %d>>\nstream\n%s\nendstream" % (len(stream), stream),
               b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return pdf + b"trailer\n<</Size %d /Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def test_report_text_from_each_format():
    assert "Fachkraefte fehlen" in report.to_text(_pdf_with_text("Fachkraefte fehlen"))
    assert report.to_text("Der Mangel wächst.".encode("cp1252")) == "Der Mangel wächst."
    assert report.to_text("﻿Der Mangel wächst.".encode("utf-8")) == "Der Mangel wächst."

    sentence = "Der Mangel an Elektrikern wächst in allen Regionen deutlich und bleibt auch im kommenden Jahr hoch."
    page = f"""<!doctype html><html><head><title>T</title></head><body><nav>Startseite Impressum</nav>
<article><h1>Bericht</h1><p>{sentence}</p><p>{sentence}</p></article></body></html>"""
    assert sentence in report.to_text(page.encode("utf-8"))

    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    blank = io.BytesIO()
    writer.write(blank)
    for data, word in [(blank.getvalue(), "scanned"), (b"", "empty"), (b"%PDF-1.4 broken", "PDF")]:
        try:
            report.to_text(data)
            raise AssertionError(word)
        except ValueError as e:
            assert word in str(e), (word, str(e))


def test_report_url_cache_and_errors():
    class Fake:
        def __init__(self, status=200, content=b"Der Mangel waechst."):
            self.status_code, self.content, self.url = status, content, "https://example.org/final"

    calls = []

    def get(url, headers, timeout):
        calls.append(url)
        return Fake()

    tmp = Path(tempfile.mkdtemp())
    try:
        first = report.read_url("https://example.org/r", get=get, cache_dir=tmp)
        assert first.origin == "https://example.org/r" and first.url == "https://example.org/final"
        assert report.read_url("https://example.org/r", get=get, cache_dir=tmp) == first and len(calls) == 1
        report.read_url("https://example.org/r", refresh=True, get=get, cache_dir=tmp)
        assert len(calls) == 2, "--refresh 要重新下载"

        def refused(url, headers, timeout):
            return Fake(status=403)

        def offline(url, headers, timeout):
            raise requests.ConnectionError("no route")

        kept = list(tmp.iterdir())
        for kwargs, word in [({"get": refused}, "HTTP 403"), ({"get": offline}, "no route")]:
            try:
                report.read_url("https://example.org/r", refresh=True, cache_dir=tmp, **kwargs)
                raise AssertionError(word)
            except ValueError as e:
                assert word in str(e), (word, str(e))
        assert list(tmp.iterdir()) == kept and report.read_url("https://example.org/r", get=get, cache_dir=tmp) == first, \
            "失败时保留旧缓存"
        try:
            report.read_url("file:///etc/passwd", get=get, cache_dir=tmp)
            raise AssertionError("file:// should be rejected")
        except ValueError as e:
            assert "http" in str(e)

        f = tmp / "r.txt"
        f.write_text("Der Mangel wächst.", encoding="utf-8")
        local = report.read_file(f)
        assert local.url is None and local.text == "Der Mangel wächst."
        try:
            report.read_file(tmp / "missing.pdf")
            raise AssertionError("missing file")
        except ValueError as e:
            assert "cannot read" in str(e)
    finally:
        shutil.rmtree(tmp)


def test_quote_and_number_checks():
    pdf_text = "Der Fach-\nkräftemangel  bei\nElektrikern wächst „deutlich“ um 16.270 Personen."
    assert extract.check_quote("Der Fachkräftemangel bei Elektrikern wächst", pdf_text) == "found", "PDF 断行断词不算不同"
    assert extract.check_quote('wächst "deutlich" um 16.270 Personen', pdf_text) == "found", "引号写法不同不算不同"
    assert extract.check_quote("Der Mangel bei Pflegekräften wächst stark", pdf_text) == "not_found"
    assert extract.check_quote("Der Mangel", pdf_text) == "too_short"
    assert extract.missing_numbers("缺口约 16,270 人", "um 16.270 Personen") == []
    assert extract.missing_numbers("缺口 37%，约 90 万", "plus 37 Prozent") == ["90"]


def _fake_claude(out: dict, seen: list):
    def run(cmd, input, **kwargs):
        seen.append((cmd, input, kwargs))
        result = {"is_error": False, "structured_output": out, "usage": {"input_tokens": 5, "output_tokens": 2}}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(result), stderr="")
    return run


def test_extract_writes_a_draft_and_leaves_evidence_alone():
    text = "Der Mangel an Elektrikern wächst weiter um 37 Prozent in allen Regionen."
    out = {"source": {"id": "kofa-neu", "name": "Neu", "publisher": "P", "date": "2026", "tier": "research"},
           "trend_labels": {"up": "ignored", "down": "ignored"},
           "signals": [
               {"item_id": "elektriker", "item_name": "Elektriker", "trend": "up", "claim": "增长 37%，约 90 万",
                "quote": "Der Mangel an Elektrikern wächst weiter um 37 Prozent"},
               {"item_id": "pflege", "item_name": "Pflege", "trend": "down", "claim": "在降温",
                "quote": "Dieser Satz steht nirgendwo im Bericht drin"}]}
    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "evidence.yaml"
        path.write_text("""# 这行注释不能丢
question: Wo fehlen Fachkräfte?
lang: zh
trend_labels: {up: 紧缺, down: 降温}
sources: {alt: {name: Alt, url: "https://example.org/alt", tier: official}}
items: [{id: elektriker, name: 电工, subtitle: Elektriker}]
""", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        rep = report.Report(origin="https://example.org/neu", url="https://example.org/neu", text=text, read_at="now")
        seen = []
        got = extract.propose(path, None, rep, run=_fake_claude(out, seen))
        assert path.read_text(encoding="utf-8") == before, "extract 绝不能改证据表"
        assert got["checks"] == {"found": 1, "not_found": 1, "too_short": 0} and got["added"] == 2

        cmd, prompt, kwargs = seen[0]
        assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--model") + 1] == extract.DEFAULT_MODEL
        assert "CLAUDECODE" not in kwargs["env"]
        assert prompt.index("<report>") < prompt.index("Question: Wo fehlen")
        for word in ("up means 紧缺", "down means 降温", "elektriker: 电工 (Elektriker)", "Chinese", text):
            assert word in prompt, word

        draft = yaml.safe_load(got["path"].read_text(encoding="utf-8"))
        assert got["path"].name == "evidence.proposed.yaml"
        assert draft["trend_labels"] == {"up": "紧缺", "down": "降温"}, "证据表已有的含义不让 AI 改"
        assert draft["sources"]["kofa-neu"]["url"] == "https://example.org/neu"
        first, second = draft["proposals"]
        assert first["item"] == "elektriker" and "new_item" not in first and first["decision"] == "pending"
        assert first["quote_check"] == "found" and first["claim_numbers_missing"] == ["90"]
        assert second["new_item"] and second["name"] == "Pflege" and second["quote_check"] == "not_found"

        # 第二份报告：追加进草稿，保留人填的 decision，上次新提的对象也要告诉模型
        first["decision"] = "accept"
        got["path"].write_text(yaml.safe_dump(draft, allow_unicode=True), encoding="utf-8")
        again = dict(out, signals=out["signals"][:1])
        extract.propose(path, None, rep, run=_fake_claude(again, seen))
        draft = yaml.safe_load(got["path"].read_text(encoding="utf-8"))
        assert [p["decision"] for p in draft["proposals"]] == ["accept", "pending", "pending"]
        assert list(draft["sources"]) == ["kofa-neu"], "同一个链接不重复登记来源"
        assert "pflege: Pflege" in seen[1][1]
        got["path"].unlink()

        # 证据表里已登记的链接，直接沿用它的 id
        known = report.Report(origin="x", url="https://example.org/alt", text=text, read_at="now")
        got = extract.propose(path, None, known, run=_fake_claude(out, seen))
        assert got["source"] == "alt" and not yaml.safe_load(got["path"].read_text(encoding="utf-8"))["sources"]

        # 问题不同的草稿不能混在一起
        try:
            extract.propose(path, "Ein anderes Thema?", rep, run=_fake_claude(out, seen))
            raise AssertionError("another question")
        except ValueError as e:
            assert "another question" in str(e)
        got["path"].unlink()

        # 证据表还不存在：按新问题生成草稿，含义由 AI 提议
        new = tmp / "neu" / "evidence.yaml"
        try:
            extract.propose(new, None, rep, run=_fake_claude(out, seen))
            raise AssertionError("needs a question")
        except ValueError as e:
            assert "--question" in str(e)
        local = report.Report(origin="r.pdf", url=None, text=text, read_at="now")
        got = extract.propose(new, "Nur Elektriker?", local, source_url="https://example.org/pub",
                              run=_fake_claude(out, seen))
        draft = yaml.safe_load(got["path"].read_text(encoding="utf-8"))
        assert not new.exists() and draft["lang"] == "en" and draft["trend_labels"]["up"] == "ignored"
        assert draft["sources"]["kofa-neu"]["url"] == "https://example.org/pub"
        assert all(p["new_item"] for p in draft["proposals"])
    finally:
        shutil.rmtree(tmp)


def test_extract_reports_claude_failures():
    def fails(stdout="", stderr="", code=1):
        return lambda cmd, input, **kwargs: subprocess.CompletedProcess(cmd, code, stdout=stdout, stderr=stderr)

    def missing(cmd, input, **kwargs):
        raise FileNotFoundError

    def slow(cmd, input, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    for run, word in [(fails(stderr="not logged in"), "not logged in"),
                      (fails(stdout=json.dumps({"is_error": True, "result": "limit reached"})), "limit reached"),
                      (fails(stdout=json.dumps({"is_error": False, "result": "text"}), code=0), "no result"),
                      (missing, "not found"), (slow, "did not finish")]:
        try:
            extract.call_claude("hi", "m", run=run)
            raise AssertionError(word)
        except ValueError as e:
            assert word in str(e), (word, str(e))

    from refinery.__main__ import main
    tmp = Path(tempfile.mkdtemp())
    try:
        assert main(["extract", str(tmp / "evidence.yaml"), "--question", "Q", "--file", str(tmp / "no.pdf")]) == 1
    finally:
        shutil.rmtree(tmp)


def _proposal(item, decision="pending", check="found", **extra):
    return {"item": item, "source": "neu", "trend": "up", "claim": f"{item} 紧缺", "quote": "Der Mangel wächst überall",
            "quote_check": check, "decision": decision, **extra}


def _write_draft(evidence_path, proposals, **extra):
    draft = {"question": "Wo fehlen Fachkräfte?", "lang": "zh", "trend_labels": {"up": "紧缺", "down": "降温"},
             "sources": {"neu": {"name": "Neu", "publisher": "P", "url": "https://example.org/neu", "date": "2026",
                                 "tier": "research"}},
             "proposals": proposals, **extra}
    extract.draft_path(evidence_path).write_text(yaml.safe_dump(draft, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_merge_keeps_comments_and_only_adds():
    for name in ("job-shortage-de", "renewables-eu"):
        raw = (ROOT / "examples" / name / "evidence.yaml").read_text(encoding="utf-8")
        y = merge._round_trip(raw)
        buf = io.StringIO()
        y.dump(y.load(raw), buf)
        assert buf.getvalue() == raw, f"{name}: 读写一遍不能改动任何内容"

    assert merge.clean_quote("Hu-\nmanmedizin, Bau-\nund Hans-\nBöckler-Stiftung\n  bleiben") == \
        "Humanmedizin, Bau- und Hans-Böckler-Stiftung bleiben"

    tmp = Path(tempfile.mkdtemp())
    try:
        path = tmp / "evidence.yaml"
        path.write_text("""# 这行注释不能丢
question: Q
lang: zh
sources:
  alt: {name: Alt, url: "https://example.org/alt", tier: official}   # 这条也不能丢
items:
- id: elektriker
  name: 电工
- id: pflege
  name: 护理
  signals:
  - source: alt
    trend: down
    claim: 旧信号
""", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        _write_draft(path, [
            _proposal("elektriker"),                                          # 没标记 → 接受
            _proposal("pflege", decision="reject"),                           # 人挑出的错 → 丢弃
            _proposal("tierarzt", name="兽医", new_item=True, decision="accept"),
            _proposal("lehrer", name="教师", new_item=True, check="not_found"),     # 被标记 → 留下
            _proposal("arzt", name="医生", new_item=True, claim_numbers_missing=["90"]),   # 被标记 → 留下
        ])

        # 不加 --accept-rest：只写明确接受的
        got = merge.merge(path)
        assert (got["merged"], got["new_items"], got["rejected"], got["kept"]) == (1, 1, 1, 3)
        after = path.read_text(encoding="utf-8")
        assert "# 这行注释不能丢" in after and "# 这条也不能丢" in after
        remaining = iter(after.splitlines())
        assert all(line in remaining for line in before.splitlines()), "原有的行要按顺序原样保留"
        ev = evidence.load(path)
        assert "neu" in ev.sources and next(it for it in ev.items if it["id"] == "tierarzt")["name"] == "兽医"

        got = merge.merge(path, accept_rest=True)
        assert (got["merged"], got["rejected"], got["kept"]) == (1, 0, 2), "被标记的条目不会被自动接受"
        ev = evidence.load(path)
        assert evidence.score(next(it for it in ev.items if it["id"] == "elektriker"))[0] == 1
        assert evidence.score(next(it for it in ev.items if it["id"] == "pflege"))[0] == -1, "拒绝的条目不计分"
        signal = next(it for it in ev.items if it["id"] == "elektriker")["signals"][0]
        assert list(signal) == ["source", "trend", "claim", "quote"]
        assert not any(it["id"] in ("lehrer", "arzt") for it in ev.items)

        # 被标记的条目，人明确决定后才能进
        draft = yaml.safe_load(extract.draft_path(path).read_text(encoding="utf-8"))
        assert [p["item"] for p in draft["proposals"]] == ["lehrer", "arzt"] and "neu" in draft["sources"]
        draft["proposals"][0]["decision"] = "accept"
        draft["proposals"][1]["decision"] = "accepted"
        extract.draft_path(path).write_text(yaml.safe_dump(draft, allow_unicode=True), encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        try:
            merge.merge(path)
            raise AssertionError("should fail")
        except ValueError as e:
            assert "accept_unverified" in str(e) and "'accepted'" in str(e), str(e)
        assert path.read_text(encoding="utf-8") == before

        draft["proposals"][0]["decision"] = "accept_unverified"
        draft["proposals"][1]["decision"] = "accept"
        extract.draft_path(path).write_text(yaml.safe_dump(draft, allow_unicode=True), encoding="utf-8")
        got = merge.merge(path)
        assert got["merged"] == 2 and got["kept"] == 0 and not extract.draft_path(path).exists(), "处理完就删草稿"
        try:
            merge.merge(path)
            raise AssertionError("no draft")
        except ValueError as e:
            assert "run extract first" in str(e)
    finally:
        shutil.rmtree(tmp)


def test_merge_validates_before_writing_and_can_create_a_file():
    tmp = Path(tempfile.mkdtemp())
    try:
        # 合并结果不合法（信号方向写错）：证据表和草稿都不变，也不留临时文件
        path = tmp / "evidence.yaml"
        path.write_text("sources: {}\nitems: [{id: a, name: A}]\n", encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        _write_draft(path, [_proposal("a", decision="accept", trend="sideways")])
        draft_before = extract.draft_path(path).read_text(encoding="utf-8")
        try:
            merge.merge(path)
            raise AssertionError("should fail")
        except ValueError as e:
            assert "sideways" in str(e) and str(path) in str(e), str(e)
        assert path.read_text(encoding="utf-8") == before
        assert extract.draft_path(path).read_text(encoding="utf-8") == draft_before
        assert sorted(f.name for f in tmp.iterdir()) == ["evidence.proposed.yaml", "evidence.yaml"]

        # 本地文件没给 --source-url：来源没有网址，校验拦下，提示人补上
        draft = yaml.safe_load(draft_before)
        draft["proposals"][0]["trend"] = "up"
        draft["sources"]["neu"]["url"] = None
        extract.draft_path(path).write_text(yaml.safe_dump(draft, allow_unicode=True), encoding="utf-8")
        try:
            merge.merge(path)
            raise AssertionError("should fail")
        except ValueError as e:
            assert "missing url" in str(e), str(e)

        # 证据表还不存在：按新问题创建，能直接 build
        new = tmp / "neu" / "evidence.yaml"
        new.parent.mkdir()
        _write_draft(new, [_proposal("elektriker", name="电工", new_item=True)])
        from refinery.__main__ import main
        assert main(["merge", str(new), "--accept-rest"]) == 0
        ev = evidence.load(new)
        assert ev.question == "Wo fehlen Fachkräfte?" and ev.lang == "zh" and ev.trend_labels["up"] == "紧缺"
        assert main(["build", str(new), "--html", "--out", str(tmp / "o.html")]) == 0
        assert "↑ 紧缺" in (tmp / "o.html").read_text(encoding="utf-8")
        assert main(["merge", str(new)]) == 1, "没有草稿时报错"
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
