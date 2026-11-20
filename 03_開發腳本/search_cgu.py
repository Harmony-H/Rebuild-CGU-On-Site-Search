#!/usr/bin/env python3
"""搜尋長庚規章、公告與 PDF 內文的 SQLite FTS5 查詢工具。"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def fts_query(value: str) -> str:
    terms = re.findall(r"[\w\u3400-\u9fff]+", value, re.UNICODE)
    if not terms:
        raise ValueError("搜尋字串沒有可用內容")
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def search_terms(value: str) -> tuple[list[str], list[str]]:
    """回傳 FTS 詞與中文短詞 LIKE 詞；補足 FTS5 trigram 不索引兩字詞的限制。"""
    raw = re.findall(r"[\w\u3400-\u9fff]+", value, re.UNICODE)
    fts_terms, like_terms = [], []
    for term in raw:
        if len(term) >= 3:
            fts_terms.append(term)
        if re.fullmatch(r"[\u3400-\u9fff]+", term) and len(term) >= 4:
            like_terms.extend(term[i:i + 2] for i in range(0, len(term) - 1, 2))
        else:
            like_terms.append(term)
    return fts_terms, like_terms


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="例如：英文免修、免修門檻、申請流程")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "04_爬蟲資料" / "cgu_data" / "cgu.db"))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    fts_terms, like_terms = search_terms(args.query)
    match = " AND ".join('"' + term.replace('"', '""') + '"' for term in fts_terms)
    blob = "coalesce(i.subject,'') || ' ' || coalesce(i.title,'') || ' ' || coalesce(i.content,'') || ' ' || coalesce(i.pdf_text,'') || ' ' || coalesce(i.keywords,'') || ' ' || coalesce(i.applicable_audience,'') || ' ' || coalesce(i.unit,'')"
    like_clauses = [f"{blob} LIKE ?" for _ in like_terms]
    fallback = " AND ".join(like_clauses) if like_clauses else "0"
    sql = """
        SELECT i.subject, i.unit, i.document_type, i.applicable_audience,
               i.published_date, i.original_url, i.is_valid,
               i.title AS _title, i.content AS _content, i.pdf_text AS _pdf_text,
               coalesce(nullif(snippet(items_fts, -1, '【', '】', '…', 32), ''),
                        substr(coalesce(i.pdf_text, i.content, i.title), 1, 320)) AS snippet,
               bm25(items_fts) AS rank
        FROM items AS i
        LEFT JOIN items_fts ON i.rowid = items_fts.rowid
        WHERE i.rowid IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)
           OR (""" + fallback + """)
        ORDER BY rank
        LIMIT ?
    """
    params = [match or "__no_fts_term__"] + [f"%{term}%" for term in like_terms] + [max(1, args.limit)]
    with sqlite3.connect(args.db) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute(sql, params)]
    for row in rows:
        source = " ".join(row.pop(key) or "" for key in ("_title", "_content", "_pdf_text"))
        if "【" not in row["snippet"] and like_terms:
            positions = [source.find(term) for term in like_terms if source.find(term) >= 0]
            if positions:
                pos = min(positions)
                row["snippet"] = source[max(0, pos - 80):pos + 320]
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for n, row in enumerate(rows, 1):
            print(f"[{n}] {row['subject']} | {row['unit']}")
            print(f"    {row['snippet']}")
            print(f"    {row['original_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
