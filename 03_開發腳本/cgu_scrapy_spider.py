#!/usr/bin/env python3
"""用 Scrapy 抓取長庚大學網頁，並抽取 PDF 文字內容。

這個 spider 沿用 cgu_crawler.py 的欄位與解析器，額外將 PDF 內文放入
``pdf_text``，再用內文補強關鍵字與適用對象。掃描型圖片 PDF 沒有文字層，
需要另外接 OCR 才能辨識。

執行：
  python cgu_scrapy_spider.py --output ..\\04_爬蟲資料\\cgu_data --max-pages 2000
  python cgu_scrapy_spider.py --download-files
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import scrapy
from bs4 import BeautifulSoup
from pypdf import PdfReader
from scrapy.crawler import CrawlerProcess

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from cgu_crawler import (  # noqa: E402
    DEFAULT_SEEDS,
    FILE_EXTS,
    HOST,
    DETAIL_PATH,
    Item,
    attachment_links,
    canonical,
    clean,
    extract_items,
    fill_standard_fields,
    is_same_site,
    looks_interesting,
)
from sqlite_fts5 import rebuild_fts5  # noqa: E402


def is_pdf_url(url: str, label: str = "") -> bool:
    path = urlparse(url).path.lower()
    return (
        path.endswith(".pdf")
        or ".pdf" in label.lower()
        or "/serverfile/" in path
        or "/uploads/" in path
    )


def is_pdf_response(response: scrapy.http.Response) -> bool:
    content_type = response.headers.get(b"Content-Type", b"").decode("latin1").lower()
    # ServerFile/Uploads endpoints frequently omit the extension and may
    # return Word, Excel, ZIP, or image files.  Only parse them as PDF when
    # the response advertises PDF or carries the PDF magic header.
    path = urlparse(response.url).path.lower()
    return "application/pdf" in content_type or response.body[:4] == b"%PDF" or path.endswith(".pdf")


def extract_pdf(body: bytes) -> tuple[str, int]:
    """回傳 PDF 文字與頁數；文字層不存在時回傳空字串。"""
    reader = PdfReader(io.BytesIO(body), strict=False)
    pages = len(reader.pages)
    chunks = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return clean("\n".join(chunks)), pages


def merge_item(store: dict[str, Item], item: Item) -> Item:
    old = store.get(item.url)
    if old is None:
        store[item.url] = item
        return item
    scalar = ("title", "unit", "date", "category", "number", "review_meeting", "content",
              "pdf_text", "pdf_pages", "subject", "document_type", "applicable_audience",
              "published_date", "last_updated_date", "original_url")
    for name in scalar:
        value = getattr(item, name)
        if value and (not getattr(old, name) or name in {"content", "pdf_text", "pdf_pages"}):
            setattr(old, name, value)
    if item.keywords:
        old.keywords = sorted(set(old.keywords).union(item.keywords))
    if item.attachments:
        old.attachments = item.attachments
    if item.is_valid is False:
        old.is_valid = False
    old.kind = item.kind if item.kind.endswith("detail") else old.kind
    old.crawled_at = item.crawled_at
    return old


class CguSpider(scrapy.Spider):
    name = "cgu_regulations_and_announcements"
    allowed_domains = [HOST]

    custom_settings = {
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        "DOWNLOAD_TIMEOUT": 45,
        "FEED_EXPORT_ENCODING": "utf-8",
        "LOG_LEVEL": "WARNING",
    }

    def __init__(self, seed=None, output="cgu_data", max_pages=2000, delay=0.35,
                 download_files=False, concurrency=8, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = seed if isinstance(seed, list) else (seed or DEFAULT_SEEDS)
        self.output = Path(output).resolve()
        self.max_pages = int(max_pages)
        self.delay = float(delay)
        self.download_files = bool(download_files)
        self.pages_seen = 0
        self.items_by_url: dict[str, Item] = {}
        self.errors: list[dict] = []
        self.pdf_seen: set[str] = set()
        self.pdf_parsed = 0

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(canonical(url), callback=self.parse, errback=self.errback,
                                 dont_filter=False)

    def parse(self, response: scrapy.http.Response, **kwargs):
        if self.pages_seen >= self.max_pages:
            return
        if is_pdf_response(response):
            yield from self.parse_pdf(response)
            return
        content_type = response.headers.get(b"Content-Type", b"").decode("latin1").lower()
        if "html" not in content_type and not response.text.lstrip().startswith("<"):
            return
        self.pages_seen += 1
        soup = BeautifulSoup(response.text, "html.parser")
        for item in extract_items(soup, response.url):
            current = merge_item(self.items_by_url, item)
            for att in item.attachments:
                if not is_pdf_url(att["url"], att.get("name", "")) or att["url"] in self.pdf_seen:
                    continue
                self.pdf_seen.add(att["url"])
                yield scrapy.Request(att["url"], callback=self.parse_pdf, errback=self.errback,
                                     meta={"parent_url": current.url, "pdf_name": att.get("name", "")})

        from_root = urlparse(response.url).path.rstrip("/") == ""
        for anchor in soup.select("a[href]"):
            href = canonical(urljoin(response.url, anchor.get("href", "")))
            label = clean(anchor.get_text(" ", strip=True))
            if not is_same_site(href) or not (from_root or looks_interesting(href, label)):
                continue
            if is_pdf_url(href, label):
                if href not in self.pdf_seen:
                    self.pdf_seen.add(href)
                    yield scrapy.Request(href, callback=self.parse_pdf, errback=self.errback,
                                         meta={"parent_url": response.url, "pdf_name": label})
            else:
                yield scrapy.Request(href, callback=self.parse, errback=self.errback)

    def parse_pdf(self, response: scrapy.http.Response):
        # ServerFile/Uploads URLs often have no .pdf suffix.  Probe them, but
        # silently skip a non-PDF response instead of treating it as a crawl
        # error.
        if not is_pdf_response(response):
            return
        try:
            text, pages = extract_pdf(response.body)
        except Exception as exc:
            self.errors.append({"url": response.url, "error": f"PDF extraction: {exc}"})
            return
        self.pdf_parsed += 1
        parent_url = response.meta.get("parent_url")
        if parent_url in self.items_by_url:
            item = self.items_by_url[parent_url]
        else:
            name = clean(response.meta.get("pdf_name") or Path(urlparse(response.url).path).name)
            item = merge_item(self.items_by_url, Item(
                url=response.url, source_url=parent_url or response.url, kind="regulation",
                title=name, original_url=response.url,
                crawled_at=datetime.now(timezone.utc).isoformat()))
        if text:
            item.pdf_text = clean("\n\n".join(x for x in (item.pdf_text, text) if x))
        item.pdf_pages = (item.pdf_pages or 0) + pages
        fill_standard_fields(item, text)
        if self.download_files:
            files_dir = self.output / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            path = files_dir / f"{hashlib.sha256(response.url.encode()).hexdigest()[:16]}.pdf"
            if not path.exists():
                path.write_bytes(response.body)

    def errback(self, failure):
        request = failure.request
        self.errors.append({"url": request.url, "error": repr(failure.value)})

    def closed(self, reason):
        self.output.mkdir(parents=True, exist_ok=True)
        records = [asdict(fill_standard_fields(item, item.pdf_text)) for item in self.items_by_url.values()]
        fields = list(Item.__dataclass_fields__.keys())
        (self.output / "items.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.output / "items.jsonl").open("w", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        with (self.output / "items.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
            for row in records:
                writer.writerow({**row, "attachments": json.dumps(row["attachments"], ensure_ascii=False),
                                 "keywords": json.dumps(row["keywords"], ensure_ascii=False)})
        standard = [{
            "單位": row["unit"], "主題": row["subject"], "文件類型": row["document_type"],
            "適用對象": row["applicable_audience"], "關鍵字": "、".join(row["keywords"]),
            "發布日期": row["published_date"], "最後更新日期": row["last_updated_date"],
            "原始連結": row["original_url"], "是否仍有效": row["is_valid"],
        } for row in records]
        (self.output / "standard_items.json").write_text(json.dumps(standard, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.output / "standard_items.csv").open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(standard[0]) if standard else ["單位", "主題", "文件類型", "適用對象", "關鍵字", "發布日期", "最後更新日期", "原始連結", "是否仍有效"])
            writer.writeheader(); writer.writerows(standard)
        db = sqlite3.connect(self.output / "cgu.db")
        db.execute("CREATE TABLE IF NOT EXISTS items (url TEXT PRIMARY KEY, source_url TEXT, kind TEXT, title TEXT, unit TEXT, date TEXT, category TEXT, number TEXT, review_meeting TEXT, content TEXT, attachments TEXT, crawled_at TEXT)")
        existing = {row[1] for row in db.execute("PRAGMA table_info(items)")}
        for name in fields:
            if name not in existing:
                db.execute(f'ALTER TABLE items ADD COLUMN "{name}" {"INTEGER" if name == "is_valid" else "TEXT"}')
        def db_value(row, name):
            return json.dumps(row[name], ensure_ascii=False) if name in {"attachments", "keywords"} else row[name]
        db.executemany(f"INSERT OR REPLACE INTO items ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                       [tuple(db_value(row, name) for name in fields) for row in records])
        rebuild_fts5(db)
        db.close()
        (self.output / "errors.json").write_text(json.dumps(self.errors, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.output / "run.json").write_text(json.dumps({
            "finished_at": datetime.now(timezone.utc).isoformat(), "engine": "scrapy",
            "pages": self.pages_seen, "items": len(records),
            "pdf_links": len(self.pdf_seen), "pdfs": self.pdf_parsed,
            "errors": len(self.errors), "reason": reason,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Scrapy 完成：{self.pages_seen} 個頁面、{len(records)} 筆資料、{self.pdf_parsed} 個 PDF、{len(self.errors)} 個錯誤；輸出：{self.output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", default=None, help="起始網址，可重複指定")
    parser.add_argument("--output", default=str(SCRIPT_DIR.parent / "04_爬蟲資料" / "cgu_data"))
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--download-files", action="store_true")
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="忽略 robots.txt（PDF 若被校方 robots.txt 擋下時才使用）",
    )
    args = parser.parse_args()
    settings = {
        "ROBOTSTXT_OBEY": not args.ignore_robots,
        "LOG_LEVEL": "WARNING",
        "USER_AGENT": "CGU-ScrapyCrawler/1.0 (+https://www.cgu.edu.tw/)",
        "DOWNLOAD_DELAY": args.delay,
        "CONCURRENT_REQUESTS": args.concurrency,
    }
    process = CrawlerProcess(settings=settings)
    process.crawl(CguSpider, seed=args.seed or DEFAULT_SEEDS, output=args.output,
                  max_pages=args.max_pages, delay=args.delay,
                  concurrency=args.concurrency, download_files=args.download_files)
    process.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
