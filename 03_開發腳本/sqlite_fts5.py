"""SQLite FTS5 schema helpers shared by the CGU crawlers."""

import sqlite3

FTS5_COLUMNS = ("subject", "title", "content", "pdf_text", "keywords", "applicable_audience", "unit")


def rebuild_fts5(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE IF EXISTS items_fts")
    columns = ", ".join(FTS5_COLUMNS)
    db.execute(
        f"CREATE VIRTUAL TABLE items_fts USING fts5({columns}, "
        "content='items', content_rowid='rowid', tokenize='trigram')"
    )
    db.execute("INSERT INTO items_fts(items_fts) VALUES ('rebuild')")
    db.commit()
