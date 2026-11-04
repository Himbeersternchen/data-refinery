"""Read a report into plain text, from a URL or a local file.
把一份报告读成纯文本，来源可以是链接，也可以是本地文件。

Which reports to read is the user's choice, and so is checking that a site's terms allow it.
If a site refuses (HTTP 403 and so on) we report that and stop; we never try to get around it.
读哪些报告由使用者决定，是否符合网站条款也由使用者确认。网站拒绝访问时只报错，不绕过。

Text read from a URL is cached in .cache/reports/ (not committed), so asking a new question about
the same report needs no new download. --refresh downloads it again.
链接读到的正文存在 .cache/reports/（不进仓库），换个问题再提炼时不用重新下载。
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
import trafilatura
from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError

CACHE_DIR = Path(".cache/reports")
HEADERS = {"User-Agent": "data-refinery/0.1 (+https://github.com/Himbeersternchen/data-refinery)"}


@dataclass
class Report:
    origin: str          # 使用者给的链接或路径
    url: str | None      # 实际读到的地址（跳转之后）；本地文件没有
    text: str
    read_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pdf_text(data: bytes) -> str:
    try:
        return "\n\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    except (PyPdfError, DependencyError) as e:
        raise ValueError(f"cannot read this PDF: {e}") from None


def to_text(data: bytes) -> str:
    """按内容开头判断类型，不看网址后缀：BA 的 PDF 链接结尾是 ?__blob=publicationFile。"""
    if data[:5] == b"%PDF-":
        text = _pdf_text(data)
        hint = "a scanned PDF has no text layer; run OCR on it first"
    elif b"<html" in data[:2000].lower():
        text = trafilatura.extract(data, include_tables=True, favor_recall=True) or ""
        hint = "the page may need JavaScript; save its text to a file and use --file"
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252", errors="replace")
        hint = "the file is empty"
    if not text.strip():
        raise ValueError(f"no text found: {hint}")
    return text


def read_file(path: str | Path) -> Report:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise ValueError(f"cannot read {path}: {e.strerror or e}") from None
    try:
        return Report(origin=str(path), url=None, text=to_text(data), read_at=_now())
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


def read_url(url: str, refresh: bool = False, get=requests.get, cache_dir: Path = CACHE_DIR) -> Report:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"{url}: only http and https links can be read; for a local file use --file")
    cached = Path(cache_dir) / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")
    if cached.exists() and not refresh:
        return Report(**json.loads(cached.read_text(encoding="utf-8")))

    try:
        r = get(url, headers=HEADERS, timeout=60)
    except requests.RequestException as e:
        raise ValueError(f"{url}: {e}") from None
    if r.status_code != 200:
        raise ValueError(f"{url}: HTTP {r.status_code}. If the site refuses programs, "
                         "download the report yourself and use --file")
    try:
        report = Report(origin=url, url=r.url, text=to_text(r.content), read_at=_now())
    except ValueError as e:
        raise ValueError(f"{url}: {e}") from None
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=1), encoding="utf-8")
    return report
