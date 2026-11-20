#!/usr/bin/env python3
"""為既有 cgu.db 建立 SQLite FTS5 中文全文索引。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sqlite_fts5 import rebuild_fts5  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(SCRIPT_DIR.parent / "04_爬蟲資料" / "cgu_data" / "cgu.db"))
    args = parser.parse_args()
    db_path = Path(args.db)
    with sqlite3.connect(db_path) as db:
        rebuild_fts5(db)
        count = db.execute("SELECT count(*) FROM items_fts").fetchone()[0]
    print(f"FTS5 完成：{count} 筆；資料庫：{db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
