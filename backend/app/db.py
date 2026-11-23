"""SQLite 索引。查询走本机库，不把数据库放在 NAS 上。"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .paths import DEFAULT_EXCLUDES, DEFAULT_EXTENSIONS


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA busy_timeout=8000")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self._migrate()
            self._ensure_defaults()
            self.conn.commit()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS folders (
              id INTEGER PRIMARY KEY,
              relative_path TEXT NOT NULL UNIQUE,
              parent_id INTEGER REFERENCES folders(id),
              name TEXT NOT NULL,
              is_book INTEGER NOT NULL DEFAULT 0,
              is_classified INTEGER NOT NULL DEFAULT 0,
              missing INTEGER NOT NULL DEFAULT 0,
              scan_gen INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS books (
              id INTEGER PRIMARY KEY,
              folder_id INTEGER REFERENCES folders(id),
              relative_path TEXT NOT NULL UNIQUE,
              folder_name TEXT NOT NULL,
              title TEXT,
              subtitle TEXT,
              series TEXT,
              series_index TEXT,
              series_index_num REAL,
              language TEXT,
              publisher TEXT,
              pubdate TEXT,
              isbn TEXT,
              description TEXT,
              rating TEXT,
              metadata_source TEXT,
              confidence TEXT,
              primary_format TEXT,
              size INTEGER,
              mtime REAL,
              head_hash TEXT,
              file_sig TEXT,
              missing INTEGER NOT NULL DEFAULT 0,
              seen INTEGER NOT NULL DEFAULT 0,
              born_gen INTEGER NOT NULL DEFAULT 0,
              lock_title INTEGER NOT NULL DEFAULT 0,
              lock_authors INTEGER NOT NULL DEFAULT 0,
              lock_tags INTEGER NOT NULL DEFAULT 0,
              lock_series INTEGER NOT NULL DEFAULT 0,
              authors_text TEXT,
              tags_text TEXT,
              suggested_category TEXT NOT NULL DEFAULT '',
              suggested_ready INTEGER NOT NULL DEFAULT 0,
              scan_gen INTEGER NOT NULL DEFAULT 0,
              name_conflict INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS authors (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS book_authors (
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              author_id INTEGER NOT NULL REFERENCES authors(id),
              PRIMARY KEY (book_id, author_id)
            );
            CREATE TABLE IF NOT EXISTS tags (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS book_tags (
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              tag_id INTEGER NOT NULL REFERENCES tags(id),
              PRIMARY KEY (book_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS book_files (
              id INTEGER PRIMARY KEY,
              book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
              relative_path TEXT NOT NULL,
              ext TEXT,
              size INTEGER,
              mtime REAL,
              is_primary INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS rules (
              id INTEGER PRIMARY KEY,
              priority INTEGER NOT NULL,
              name TEXT,
              conditions_json TEXT NOT NULL,
              target_relative_path TEXT NOT NULL,
              create_missing INTEGER NOT NULL DEFAULT 0,
              enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS jobs (
              id INTEGER PRIMARY KEY,
              type TEXT NOT NULL,
              status TEXT NOT NULL,
              progress_json TEXT,
              message TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operations (
              id INTEGER PRIMARY KEY,
              job_id INTEGER,
              kind TEXT NOT NULL,
              summary TEXT,
              created_at TEXT NOT NULL,
              undone INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS operation_items (
              id INTEGER PRIMARY KEY,
              operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
              src_path TEXT NOT NULL,
              dst_path TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              sig TEXT
            );
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_folders_parent ON folders(parent_id);
            CREATE INDEX IF NOT EXISTS idx_books_folder ON books(folder_id);
            CREATE INDEX IF NOT EXISTS idx_books_missing ON books(missing);
            CREATE INDEX IF NOT EXISTS idx_book_files_book ON book_files(book_id);
            CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag_id);
            CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);
            CREATE TABLE IF NOT EXISTS duplicate_ignores (
              signature TEXT PRIMARY KEY,
              created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(books)")}
        if "name_conflict" not in columns:
            self.conn.execute("ALTER TABLE books ADD COLUMN name_conflict INTEGER NOT NULL DEFAULT 0")
        if "suggested_category" not in columns:
            self.conn.execute("ALTER TABLE books ADD COLUMN suggested_category TEXT NOT NULL DEFAULT ''")
        if "suggested_ready" not in columns:
            self.conn.execute("ALTER TABLE books ADD COLUMN suggested_ready INTEGER NOT NULL DEFAULT 0")
            self.conn.execute("UPDATE books SET suggested_ready=1")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_books_suggested ON books(suggested_category)")
        tokenizer = "unicode61"
        try:
            self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x, tokenize='trigram')")
            self.conn.execute("DROP TABLE _fts_probe")
            tokenizer = "trigram"
        except sqlite3.OperationalError:
            tokenizer = "unicode61"
        self.conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(
              title, authors, series, tags, isbn, folder_name,
              tokenize = '{tokenizer}'
            )
            """
        )

    def _ensure_defaults(self) -> None:
        defaults = {
            "extensions": json.dumps(DEFAULT_EXTENSIONS, ensure_ascii=False),
            "excludes": json.dumps(DEFAULT_EXCLUDES, ensure_ascii=False),
            "includes": "[]",
            "inbox": "[]",
            "concurrency": "6",
            "batch_threshold": "50",
            "library_root": "",
            "last_scan_at": "",
            "smb_host": "",
            "smb_share": "",
            "smb_path": "",
            "smb_username": "",
            "smb_password": "",
            "classify_share": "",
            "classify_path": "",
            "classify_root": "",
        }
        for key, value in defaults.items():
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def setting(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self.conn.commit()


def json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]
