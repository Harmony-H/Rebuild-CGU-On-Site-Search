#!/usr/bin/env python3
"""長庚大學規章辦法與各處室公告爬蟲。

抓取集中式規章網站，以及 cgu.edu.tw 各單位網站中的公告、規章/表單頁面。
輸出 JSONL、CSV 與 SQLite；預設只保存頁面與附件中繼資料，不下載附件。

例：
  python cgu_crawler.py --output data --max-pages 2000 --download-files
  python cgu_crawler.py --seed https://www.cgu.edu.tw/personnel/ --output personnel
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from sqlite_fts5 import rebuild_fts5

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_SEEDS = [
    "https://www.cgu.edu.tw/",
    "https://www.cgu.edu.tw/regulation/RegulationManagement",
]
HOST = "www.cgu.edu.tw"
INTERESTING_PATH = re.compile(
    r"/(?:regulation|Subject|Contents|ContentSection|ServerFile|Uploads)(?:/|$)", re.I
)
DETAIL_PATH = re.compile(
    r"/(?:Subject|Contents|ContentSection)/Detail(?:/|$)"
    r"|/regulation/RegulationManagement/RegulationDetail(?:/|$)",
    re.I,
)
DATE_RE = re.compile(r"(?:19|20)\d{2}[/-]\d{1,2}[/-]\d{1,2}|\d{3}[/-]\d{1,2}[/-]\d{1,2}")
FILE_EXTS = {".pdf", ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".jpg", ".png"}


@dataclass
class Item:
    url: str
    source_url: str
    kind: str
    title: str = ""
    unit: str = ""
    date: str = ""
    category: str = ""
    number: str = ""
    review_meeting: str = ""
    content: str = ""
    pdf_text: str = ""
    pdf_pages: int = 0
    attachments: list[dict] = field(default_factory=list)
    crawled_at: str = ""
    # 對外統一欄位。舊有 title/date/category 等欄位保留，方便相容既有匯出。
    subject: str = ""
    document_type: str = ""
    applicable_audience: str = ""
    keywords: list[str] = field(default_factory=list)
    published_date: str = ""
    last_updated_date: str = ""
    original_url: str = ""
    is_valid: bool | None = None




def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonical(url: str) -> str:
    """移除 fragment、追蹤參數，同時保留網站功能所需的 nodeId/id。"""
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}]
    path = re.sub(r"/{2,}", "/", p.path) or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(query), ""))


def is_same_site(url: str) -> bool:
    p = urlparse(url)
    # 只抓主站，避免首頁導覽中的 www2、freshman 等歷史子網域
    # 解析失敗時反覆重試，造成整批工作被無效網址拖慢。
    return p.scheme in {"http", "https"} and p.netloc.lower() == HOST


def looks_interesting(url: str, anchor_text: str = "") -> bool:
    p = urlparse(url)
    if not is_same_site(url):
        return False
    if Path(p.path).suffix.lower() in FILE_EXTS:
        return True
    text = f"{p.path} {anchor_text}"
    return bool(INTERESTING_PATH.search(text) or re.search(r"公告|規章|辦法|法規|表單|最新消息|下載", text, re.I))


def session_for(ua: str) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET", "HEAD"}), raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": ua, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7"})
    return s


def attachment_links(soup: BeautifulSoup, page_url: str) -> list[dict]:
    out, seen = [], set()
    for a in soup.select("a[href]"):
        href = canonical(urljoin(page_url, a.get("href", "")))
        if not href or href in seen or not is_same_site(href):
            continue
        label = clean(a.get_text(" ", strip=True))
        ext = Path(urlparse(href).path).suffix.lower()
        # ServerFile links often have no extension; their label usually names a file.
        if ext in FILE_EXTS or "/serverfile/" in href.lower() or "/uploads/" in href.lower():
            seen.add(href)
            out.append({"name": label, "url": href, "extension": ext})
    return out


def infer_kind(url: str, text: str) -> str:
    if "/regulation" in url.lower() or "規章" in text or "辦法" in text:
        return "regulation"
    if DETAIL_PATH.search(url):
        return "announcement_detail"
    return "announcement"


def normalize_date(value: str) -> str:
    """轉成 ISO 日期；支援民國年（例如 115/11/11）。"""
    value = value or ""
    m_cn = re.search(r"((?:19|20)?\d{2,3})年(\d{1,2})月(\d{1,2})日?", value)
    m = re.search(r"((?:19|20|\d{3})\d?)[/-](\d{1,2})[/-](\d{1,2})", value)
    if m_cn:
        m = m_cn
    if not m:
        return ""
    year, month, day = (int(x) for x in m.groups())
    if year < 1911:
        year += 1911
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return ""


def infer_last_updated_date(text: str) -> str:
    """從標題中的「修訂／修正／更新」日期補足網站未提供的欄位。"""
    text = text or ""
    for pattern in (
        r"((?:19|20)?\d{2,3})年\d{1,2}月\d{1,2}日[^。；;，,]{0,8}(?:修訂|修正|更新)",
        r"((?:19|20|\d{3})\d?)[/-]\d{1,2}[/-]\d{1,2}[^。；;，,]{0,8}(?:修訂|修正|更新)",
    ):
        match = re.search(pattern, text)
        if match:
            return normalize_date(match.group(0))
    compact = re.search(r"(?<!\d)(\d{3})(\d{2})(\d{2})(?:修訂|修正|更新)", text)
    if compact:
        return normalize_date(f"{compact.group(1)}/{compact.group(2)}/{compact.group(3)}")
    return ""


def infer_audience(text: str) -> str:
    labels = [
        ("學生", r"學生|學士班|碩士班|博士班|研究生"),
        ("教師", r"教師|教授|教學助理"),
        ("職員", r"職員|職工|行政人員"),
        ("研究人員", r"研究人員|研究計畫|研究所"),
        ("外籍生", r"外國學生|僑生|外籍"),
        ("全校教職員生", r"全校|教職員生|本校教職員"),
    ]
    found = [label for label, pattern in labels if re.search(pattern, text or "")]
    return ",".join(dict.fromkeys(found))


def infer_keywords(text: str) -> list[str]:
    terms = [
        "公告", "規章", "辦法", "規定", "準則", "要點", "細則", "作業要點",
        "學生", "教師", "職員", "研究", "獎學金", "招生", "課程", "實習",
        "申訴", "請假", "宿舍", "安全", "性平", "人事", "總務", "教務", "學務",
    ]
    return [term for term in terms if term in (text or "")]


def infer_validity(text: str) -> bool | None:
    if re.search(r"廢止|失效|停止適用|撤銷", text or ""):
        return False
    # 網站仍列出但未明示狀態時不把「未判定」誤標成有效。
    return None


def fill_standard_fields(item: Item, text: str = "") -> Item:
    source = clean(" ".join(x for x in (item.title, item.content, item.pdf_text, text) if x))
    item.subject = item.subject or item.title
    item.document_type = item.document_type or ("規章辦法" if item.kind == "regulation" else "公告")
    audiences = [x for x in (item.applicable_audience.split(",") if item.applicable_audience else []) if x]
    inferred_audience = infer_audience(source)
    inferred_audiences = inferred_audience.split(",") if inferred_audience else []
    item.applicable_audience = ",".join(dict.fromkeys(audiences + inferred_audiences))
    item.keywords = sorted(set(item.keywords).union(infer_keywords(source)))
    item.published_date = item.published_date or normalize_date(item.date)
    item.last_updated_date = item.last_updated_date or infer_last_updated_date(source)
    item.original_url = item.original_url or item.url
    if item.is_valid is None:
        item.is_valid = infer_validity(source)
    return item


def extract_items(soup: BeautifulSoup, url: str) -> list[Item]:
    """抽取規章總覽/列表中的逐筆資料；詳情頁則抽取一筆。"""
    now = datetime.now(timezone.utc).isoformat()
    title = clean((soup.title.get_text(" ", strip=True) if soup.title else ""))
    body = soup.select_one("main") or soup.select_one("#main") or soup.body
    page_text = clean(body.get_text(" ", strip=True) if body else "")
    attachments = attachment_links(soup, url)
    items: list[Item] = []

    # Most cgu.edu.tw lists use an HTML table. Keep all columns to avoid losing
    # unit/date/number fields when the wording changes slightly.
    for tr in soup.select("tr"):
        cells = [clean(x.get_text(" ", strip=True)) for x in tr.select("th,td")]
        link = tr.select_one("a[href]")
        if len(cells) < 2 or not link:
            continue
        href = canonical(urljoin(url, link.get("href", "")))
        if not is_same_site(href) or Path(urlparse(href).path).suffix.lower() in FILE_EXTS:
            continue
        row_text = " | ".join(cells)
        if not (DATE_RE.search(row_text) or re.search(r"公告|規章|辦法|準則|要點|最新消息", row_text)):
            continue
        item = Item(url=href, source_url=url, kind=infer_kind(url, row_text),
                    title=clean(link.get_text(" ", strip=True)) or cells[-1], crawled_at=now)
        if cells:
            item.date = DATE_RE.search(cells[0]).group(0) if DATE_RE.search(cells[0]) else ""
        if len(cells) >= 5 and "/regulation" in url.lower():
            item.number, item.review_meeting, item.unit = cells[1], cells[3], cells[4]
            item.category = "regulation"
        elif len(cells) >= 3:
            item.category = cells[1] if len(cells) > 1 else ""
        items.append(fill_standard_fields(item, row_text))

    # A detail page has one meaningful heading and metadata, even without a table.
    if not items and (DETAIL_PATH.search(url) or "/regulation/regulationdetail/" in url.lower()):
        headings = [clean(x.get_text(" ", strip=True)) for x in soup.select("h1,h2,h3") if clean(x.get_text(" ", strip=True))]
        item = Item(url=url, source_url=url, kind=infer_kind(url, page_text),
                    title=headings[-1] if headings else title, content=page_text[:100000],
                    attachments=attachments, crawled_at=now)
        # Parse common label/value pairs from table rows and definition lists.
        for tr in soup.select("tr"):
            c = [clean(x.get_text(" ", strip=True)) for x in tr.select("th,td")]
            if len(c) >= 2:
                key, value = c[0], c[-1]
                if "制定單位" in key: item.unit = value
                elif "公告日期" in key or "發布日期" in key: item.date = value
                elif "最後更新" in key or "更新日期" in key or "修正日期" in key:
                    item.last_updated_date = normalize_date(value)
                elif "規章編號" in key or "文號" in key: item.number = value
                elif "類別" in key: item.category = value
                elif "審議會議" in key: item.review_meeting = value
        items.append(fill_standard_fields(item, page_text))
    return items


def save_attachment(session: requests.Session, att: dict, directory: Path, delay: float) -> dict:
    digest = hashlib.sha256(att["url"].encode()).hexdigest()[:16]
    ext = att.get("extension") or ".bin"
    path = directory / f"{digest}{ext}"
    if not path.exists():
        time.sleep(delay)
        r = session.get(att["url"], timeout=45)
        r.raise_for_status()
        path.write_bytes(r.content)
    return {**att, "local_path": str(path)}


def run(args: argparse.Namespace) -> int:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    files_dir = out / "files"
    if args.download_files: files_dir.mkdir(exist_ok=True)
    session = session_for(args.user_agent)
    queue = deque(canonical(x) for x in args.seed)
    visited: set[str] = set()
    items: dict[str, Item] = {}
    errors: list[dict] = []

    while queue and len(visited) < args.max_pages:
        url = queue.popleft()
        if url in visited: continue
        visited.add(url)
        try:
            time.sleep(args.delay)
            r = session.get(url, timeout=args.timeout)
            ctype = r.headers.get("content-type", "").lower()
            if r.status_code >= 400 or "text/html" not in ctype:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for item in extract_items(soup, url):
                old = items.get(item.url)
                if old is None:
                    items[item.url] = item
                else:
                    # 列表頁先發現的同一筆資料，之後會被 Detail 頁補上
                    # 內容、附件與完整欄位；保留較完整的版本。
                    for name in ("title", "unit", "date", "category", "number", "review_meeting", "content", "pdf_text", "pdf_pages",
                                 "subject", "document_type", "applicable_audience", "published_date",
                                 "last_updated_date", "original_url"):
                        if getattr(item, name) and (not getattr(old, name) or name == "content"):
                            setattr(old, name, getattr(item, name))
                    if item.keywords:
                        old.keywords = sorted(set(old.keywords).union(item.keywords))
                    if item.is_valid is False:
                        old.is_valid = False
                    if item.attachments:
                        old.attachments = item.attachments
                    old.kind = item.kind if item.kind.endswith("detail") else old.kind
                    old.crawled_at = item.crawled_at
            for a in soup.select("a[href]"):
                href = canonical(urljoin(url, a.get("href", "")))
                label = clean(a.get_text(" ", strip=True))
                # The首頁導覽連到各處室根目錄（例如 /personnel/），其 URL
                # 本身未必含 Subject/Contents；從根頁放寬一次，才能自動
                # 發現所有處室，而不必維護人工單位清單。
                from_root = urlparse(url).path.rstrip("/") == ""
                if is_same_site(href) and href not in visited and (from_root or looks_interesting(href, label)) and len(visited) + len(queue) < args.max_pages * 2:
                    queue.append(href)
        except Exception as exc:  # keep a long crawl resumable
            errors.append({"url": url, "error": str(exc)})
            if args.verbose: print(f"[WARN] {url}: {exc}", file=sys.stderr)
        if args.verbose and len(visited) % 25 == 0:
            print(f"[INFO] pages={len(visited)} queue={len(queue)} items={len(items)}", file=sys.stderr)

    if args.download_files:
        for item in items.values():
            downloaded = []
            for att in item.attachments:
                try: downloaded.append(save_attachment(session, att, files_dir, args.delay))
                except Exception as exc: downloaded.append({**att, "download_error": str(exc)})
            item.attachments = downloaded

    records = [asdict(x) for x in items.values()]
    (out / "items.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "items.jsonl").open("w", encoding="utf-8") as f:
        for row in records: f.write(json.dumps(row, ensure_ascii=False) + "\n")
    fields = list(Item.__dataclass_fields__.keys())
    with (out / "items.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for row in records:
            row = {**row,
                   "attachments": json.dumps(row["attachments"], ensure_ascii=False),
                   "keywords": json.dumps(row["keywords"], ensure_ascii=False)}
            w.writerow(row)
    # 依需求提供可直接交付給資料庫/前端的中文標準欄位檔。
    standard_fields = ["單位", "主題", "文件類型", "適用對象", "關鍵字", "發布日期", "最後更新日期", "原始連結", "是否仍有效"]
    standard_rows = [{
        "單位": row["unit"], "主題": row["subject"], "文件類型": row["document_type"],
        "適用對象": row["applicable_audience"], "關鍵字": "、".join(row["keywords"]),
        "發布日期": row["published_date"], "最後更新日期": row["last_updated_date"],
        "原始連結": row["original_url"], "是否仍有效": row["is_valid"],
    } for row in records]
    (out / "standard_items.json").write_text(json.dumps(standard_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "standard_items.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=standard_fields); w.writeheader(); w.writerows(standard_rows)
    db = sqlite3.connect(out / "cgu.db")
    db.execute("CREATE TABLE IF NOT EXISTS items (url TEXT PRIMARY KEY, source_url TEXT, kind TEXT, title TEXT, unit TEXT, date TEXT, category TEXT, number TEXT, review_meeting TEXT, content TEXT, attachments TEXT, crawled_at TEXT)")
    # 舊版 cgu.db 可能只有原始欄位；以 ALTER TABLE 平滑升級，避免定期重跑失敗。
    existing_columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
    for name in fields:
        if name not in existing_columns:
            column_type = "INTEGER" if name == "is_valid" else "TEXT"
            db.execute(f'ALTER TABLE items ADD COLUMN "{name}" {column_type}')
    def db_value(row: dict, name: str):
        if name in {"attachments", "keywords"}:
            return json.dumps(row[name], ensure_ascii=False)
        return row[name]
    db.executemany(f"INSERT OR REPLACE INTO items ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                   [tuple(db_value(row, k) for k in fields) for row in records])
    rebuild_fts5(db)
    db.close()
    (out / "errors.json").write_text(json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "run.json").write_text(json.dumps({"finished_at": datetime.now(timezone.utc).isoformat(), "pages": len(visited), "items": len(records), "errors": len(errors)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：{len(visited)} 個頁面、{len(records)} 筆資料；輸出：{out.resolve()}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", action="append", default=None, help="起始網址，可重複指定；預設校首頁與規章總覽")
    p.add_argument("--output", default=str(Path(__file__).resolve().parents[1] / "04_爬蟲資料" / "cgu_data"), help="輸出資料夾")
    p.add_argument("--max-pages", type=int, default=10000, help="最多抓取 HTML 頁數（預設 10000；可用較小值試跑）")
    p.add_argument("--delay", type=float, default=0.7, help="每次請求間隔秒數")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--download-files", action="store_true")
    p.add_argument("--user-agent", default="CGU-RegulationCrawler/1.0 (+https://www.cgu.edu.tw/)")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(); args.seed = args.seed or DEFAULT_SEEDS
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())

