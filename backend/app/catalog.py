"""目录、书目、标签与检索。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .db import Database, json_list, now
from .paths import (
    confined,
    display_path,
    excluded_name,
    join_rel,
    leaf_name,
    name_error,
    name_taken,
    parent_rel,
    rel_posix,
    replace_prefix,
    to_long,
    writable_dir,
)

SORTS = {
    "name": "b.folder_name COLLATE NOCASE",
    "title": "b.title COLLATE NOCASE",
    "author": "b.authors_text COLLATE NOCASE",
    "size": "b.size",
    "format": "b.primary_format COLLATE NOCASE",
    "path": "b.relative_path COLLATE NOCASE",
    "mtime": "b.mtime",
    "series": "b.series COLLATE NOCASE, b.series_index_num, b.title COLLATE NOCASE",
}
SOURCE_RANK = {"filename": 0, "mobi": 1, "pdf": 2, "epub": 3, "opf": 4, "manual": 5}


def series_num(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def refresh_fts(conn, book_id: int) -> None:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    conn.execute("DELETE FROM book_fts WHERE rowid=?", (book_id,))
    if not row or row["missing"]:
        return
    conn.execute(
        """
        INSERT INTO book_fts(rowid, title, authors, series, tags, isbn, folder_name)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_id,
            row["title"] or "",
            row["authors_text"] or "",
            row["series"] or "",
            row["tags_text"] or "",
            row["isbn"] or "",
            row["folder_name"] or "",
        ),
    )


def _link_names(conn, book_id: int, table: str, link: str, id_col: str, names: list[str]) -> str:
    cleaned: list[str] = []
    for name in names:
        name = (name or "").strip()
        if name and name not in cleaned:
            cleaned.append(name)
    conn.execute(f"DELETE FROM {link} WHERE book_id=?", (book_id,))
    for name in cleaned:
        conn.execute(f"INSERT INTO {table}(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
        row = conn.execute(f"SELECT id FROM {table} WHERE name=?", (name,)).fetchone()
        conn.execute(
            f"INSERT INTO {link}(book_id, {id_col}) VALUES (?, ?)",
            (book_id, row["id"]),
        )
    return "、".join(cleaned)


def set_authors(conn, book_id: int, names: list[str]) -> None:
    text = _link_names(conn, book_id, "authors", "book_authors", "author_id", names)
    conn.execute("UPDATE books SET authors_text=? WHERE id=?", (text, book_id))


def set_tags(conn, book_id: int, names: list[str]) -> None:
    text = _link_names(conn, book_id, "tags", "book_tags", "tag_id", names)
    conn.execute("UPDATE books SET tags_text=? WHERE id=?", (text, book_id))


def _rewrite_text(conn, book_id: int) -> None:
    authors = conn.execute(
        """
        SELECT a.name FROM authors a
        JOIN book_authors ba ON ba.author_id = a.id
        WHERE ba.book_id=? ORDER BY a.name
        """,
        (book_id,),
    ).fetchall()
    tags = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN book_tags bt ON bt.tag_id = t.id
        WHERE bt.book_id=? ORDER BY t.name
        """,
        (book_id,),
    ).fetchall()
    conn.execute(
        "UPDATE books SET authors_text=?, tags_text=? WHERE id=?",
        ("、".join(row["name"] for row in authors), "、".join(row["name"] for row in tags), book_id),
    )
    refresh_fts(conn, book_id)


def apply_parsed(conn, book_id: int, meta: dict) -> None:
    row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not row:
        return
    if meta.get("source") == "filename" and (row["metadata_source"] or "") not in ("", "filename"):
        return
    assignments: dict[str, Any] = {}
    if not row["lock_title"]:
        if meta.get("title"):
            assignments["title"] = meta["title"]
        if meta.get("subtitle"):
            assignments["subtitle"] = meta["subtitle"]
    if not row["lock_series"]:
        if meta.get("series"):
            assignments["series"] = meta["series"]
        if meta.get("series_index"):
            assignments["series_index"] = str(meta["series_index"])
            assignments["series_index_num"] = series_num(str(meta["series_index"]))
    for column in ("language", "publisher", "pubdate", "isbn", "description", "rating"):
        if meta.get(column):
            assignments[column] = meta[column]
    if assignments or meta.get("source"):
        assignments["metadata_source"] = meta.get("source")
        assignments["confidence"] = meta.get("confidence")
    if assignments:
        sets = ", ".join(f"{key}=?" for key in assignments)
        conn.execute(f"UPDATE books SET {sets} WHERE id=?", (*assignments.values(), book_id))
    if not row["lock_authors"] and meta.get("authors"):
        set_authors(conn, book_id, list(meta["authors"]))
    if not row["lock_tags"] and meta.get("tags") is not None and meta.get("source") != "filename":
        if meta.get("tags") or meta.get("source") in {"opf", "epub", "pdf"}:
            set_tags(conn, book_id, list(meta.get("tags") or []))
    refresh_fts(conn, book_id)


def clear_index(conn) -> None:
    conn.execute("DELETE FROM book_files")
    conn.execute("DELETE FROM book_authors")
    conn.execute("DELETE FROM book_tags")
    conn.execute("DELETE FROM book_fts")
    conn.execute("DELETE FROM books")
    conn.execute("DELETE FROM folders")


def clear_scan_records(db: Database) -> dict:
    """清掉本机扫描索引和任务记录。不删除书库里的文件。"""
    with db.transaction() as conn:
        books = conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"]
        clear_index(conn)
        conn.execute("DELETE FROM authors")
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM jobs")
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('last_scan_at', '') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    removed = 0
    covers = db.path.parent / "covers"
    if covers.is_dir():
        for item in covers.glob("*.jpg"):
            if db.path.stem == "shelf":
                if not item.name.startswith("shelf-"):
                    continue
            elif item.name.startswith("shelf-"):
                continue
            try:
                item.unlink()
                removed += 1
            except OSError:
                continue
    if db.path.stem != "shelf":
        logs = db.path.parent / "logs"
        if logs.is_dir():
            for item in logs.glob("job-*.log"):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    continue
    return {"books": books, "removed_files": removed}


def sync_shelf(main: Database, shelf: Database) -> None:
    """让分类库的根目录指向 books 共享，不改书库索引。"""
    root = main.setting("classify_root").strip()
    if root and shelf.setting("library_root") != root:
        shelf.set_setting("library_root", root)
    for key in ("extensions", "excludes", "includes"):
        value = main.setting(key)
        if value and shelf.setting(key) != value:
            shelf.set_setting(key, value)


def _share_prefix_count(conn, table: str, share: str) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE relative_path=? OR substr(relative_path, 1, ?)=?",
            (share, len(share) + 1, share + "/"),
        ).fetchone()["n"]
    )


def split_mixed_index(main: Database, shelf: Database) -> None:
    """把曾经写进书库索引的 books/ 记录搬到分类库，只做一次。"""
    share = main.setting("classify_share").strip().strip("/") or "books"
    if main.setting("shelf_split") != "1":
        with main.transaction() as conn:
            node = conn.execute("SELECT id FROM folders WHERE relative_path=?", (share,)).fetchone()
            children = []
            if node:
                children = conn.execute(
                    "SELECT relative_path, name FROM folders WHERE parent_id=? ORDER BY length(relative_path)",
                    (node["id"],),
                ).fetchall()
        sync_shelf(main, shelf)
        if children:
            with shelf.transaction() as conn:
                parent_id = root_folder(conn, shelf.setting("library_root") or share)
            for child in children:
                transfer_tree(main, shelf, child["relative_path"], child["name"], parent_id)
        main.set_setting("shelf_split", "1")
    with main.transaction() as conn:
        if _share_prefix_count(conn, "books", share):
            return
        if _share_prefix_count(conn, "folders", share):
            conn.execute(
                "DELETE FROM folders WHERE relative_path=? OR substr(relative_path, 1, ?)=?",
                (share, len(share) + 1, share + "/"),
            )


def transfer_tree(src: Database, dst: Database, src_prefix: str, dst_prefix: str, parent_id: int) -> None:
    """把一棵目录从书库索引搬到分类索引，或反向搬回。两个库的编号互不影响。"""
    src_prefix = (src_prefix or "").strip("/")
    dst_prefix = (dst_prefix or "").strip("/")
    if not src_prefix:
        raise ValueError("不能搬移书库根")
    with src.transaction() as conn:
        folders = conn.execute(
            """
            SELECT * FROM folders
            WHERE relative_path=? OR substr(relative_path, 1, ?)=?
            ORDER BY length(relative_path), relative_path
            """,
            (src_prefix, len(src_prefix) + 1, src_prefix + "/"),
        ).fetchall()
        books = conn.execute(
            """
            SELECT * FROM books
            WHERE relative_path=? OR substr(relative_path, 1, ?)=?
            """,
            (src_prefix, len(src_prefix) + 1, src_prefix + "/"),
        ).fetchall()
        book_ids = [int(row["id"]) for row in books]
        files = []
        authors: dict[int, list[str]] = {}
        tags: dict[int, list[str]] = {}
        if book_ids:
            marks = ",".join("?" for _ in book_ids)
            files = conn.execute(
                f"SELECT * FROM book_files WHERE book_id IN ({marks})",
                book_ids,
            ).fetchall()
            for row in conn.execute(
                f"""
                SELECT ba.book_id, a.name FROM book_authors ba
                JOIN authors a ON a.id=ba.author_id
                WHERE ba.book_id IN ({marks})
                """,
                book_ids,
            ):
                authors.setdefault(int(row["book_id"]), []).append(row["name"])
            for row in conn.execute(
                f"""
                SELECT bt.book_id, t.name FROM book_tags bt
                JOIN tags t ON t.id=bt.tag_id
                WHERE bt.book_id IN ({marks})
                """,
                book_ids,
            ):
                tags.setdefault(int(row["book_id"]), []).append(row["name"])
    if not folders and not books:
        return
    id_map: dict[int, int] = {}
    with dst.transaction() as conn:
        for folder in folders:
            new_rel = replace_prefix(folder["relative_path"], src_prefix, dst_prefix)
            if new_rel is None:
                continue
            new_parent = parent_id if folder["relative_path"] == src_prefix else id_map.get(folder["parent_id"])
            name = leaf_name(new_rel) or folder["name"]
            existing = conn.execute("SELECT id FROM folders WHERE relative_path=?", (new_rel,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE folders SET parent_id=?, name=?, is_book=?, missing=0 WHERE id=?",
                    (new_parent, name, folder["is_book"], existing["id"]),
                )
                new_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen)
                    VALUES (?, ?, ?, ?, ?, 0, ?)
                    """,
                    (new_rel, new_parent, name, folder["is_book"], folder["is_classified"], folder["scan_gen"]),
                )
                new_id = int(cur.lastrowid)
            id_map[int(folder["id"])] = new_id
        book_map: dict[int, int] = {}
        for book in books:
            new_rel = replace_prefix(book["relative_path"], src_prefix, dst_prefix)
            if new_rel is None:
                continue
            data = dict(book)
            old_id = int(data.pop("id"))
            data["relative_path"] = new_rel
            data["folder_id"] = id_map.get(int(book["folder_id"])) if book["folder_id"] is not None else None
            data["folder_name"] = leaf_name(new_rel) or data.get("folder_name") or ""
            data["missing"] = 0
            columns = list(data)
            cur = conn.execute(
                f"INSERT INTO books({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [data[column] for column in columns],
            )
            book_map[old_id] = int(cur.lastrowid)
        for file in files:
            new_book = book_map.get(int(file["book_id"]))
            new_rel = replace_prefix(file["relative_path"], src_prefix, dst_prefix)
            if not new_book or new_rel is None:
                continue
            conn.execute(
                """
                INSERT INTO book_files(book_id, relative_path, ext, size, mtime, is_primary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_book, new_rel, file["ext"], file["size"], file["mtime"], file["is_primary"]),
            )
        for old_id, names in authors.items():
            if old_id in book_map:
                set_authors(conn, book_map[old_id], names)
        for old_id, names in tags.items():
            if old_id in book_map:
                set_tags(conn, book_map[old_id], names)
        for new_id in book_map.values():
            refresh_fts(conn, new_id)
    with src.transaction() as conn:
        for old_id in book_ids:
            conn.execute("DELETE FROM book_fts WHERE rowid=?", (old_id,))
            conn.execute("DELETE FROM books WHERE id=?", (old_id,))
        for folder in sorted(folders, key=lambda row: len(row["relative_path"]), reverse=True):
            conn.execute("DELETE FROM folders WHERE id=?", (folder["id"],))


def classify_destination(db: Database) -> tuple[str, str]:
    """分类文件夹所在的共享。已配置时用配置；只配了原始书库时，默认落到同主机的 books。"""
    share = db.setting("classify_share").strip().strip("/")
    root = db.setting("classify_root").strip()
    if root and share:
        return root, share
    host = db.setting("smb_host").strip()
    if not host:
        return "", ""
    share = share or "books"
    subpath = db.setting("classify_path").strip().strip("/\\")
    from .smb import build_unc, connect_share

    target = build_unc(host, share, subpath)
    if not os.path.isdir(target):
        connect_share(host, share, db.setting("smb_username"), db.setting("smb_password"), force=False)
    if not os.path.isdir(target):
        raise ValueError("分类共享 books 不存在")
    if not writable_dir(target):
        raise ValueError("分类共享 books 不可写")
    if db.setting("classify_share") != share:
        db.set_setting("classify_share", share)
    if db.setting("classify_root") != target:
        db.set_setting("classify_root", target)
    return target, share


def ensure_connected(db: Database) -> None:
    host = db.setting("smb_host").strip()
    username = db.setting("smb_username")
    password = db.setting("smb_password")
    if not host:
        return
    from .smb import connect_share

    shares = []
    for key in ("smb_share", "classify_share"):
        share = db.setting(key).strip()
        if share and share not in shares:
            shares.append(share)
    for share in shares:
        connect_share(host, share, username, password, force=False)


def read_config(db: Database) -> dict:
    return {
        "library_root": db.setting("library_root"),
        "classify_root": db.setting("classify_root"),
        "classify_share": db.setting("classify_share"),
        "extensions": [item.lower().lstrip(".") for item in json_list(db.setting("extensions"))],
        "excludes": json_list(db.setting("excludes")),
        "includes": json_list(db.setting("includes")),
        "inbox": json_list(db.setting("inbox")),
        "concurrency": max(1, int(db.setting("concurrency") or "6")),
        "batch_threshold": max(1, int(db.setting("batch_threshold") or "50")),
        "last_scan_at": db.setting("last_scan_at"),
    }


def library_status(db: Database) -> dict:
    config = read_config(db)
    root = config["library_root"]
    with db.transaction() as conn:
        books = conn.execute("SELECT COUNT(*) AS n FROM books WHERE missing=0").fetchone()["n"]
        folders = conn.execute(
            "SELECT COUNT(*) AS n FROM folders WHERE missing=0 AND is_book=0"
        ).fetchone()["n"]
    readable = bool(root) and __import__("os").path.isdir(root)
    return {
        "library_root": config["library_root"],
        "extensions": config["extensions"],
        "excludes": config["excludes"],
        "includes": config["includes"],
        "inbox": config["inbox"],
        "concurrency": config["concurrency"],
        "batch_threshold": config["batch_threshold"],
        "last_scan_at": config["last_scan_at"],
        "smb_host": db.setting("smb_host"),
        "smb_share": db.setting("smb_share"),
        "smb_path": db.setting("smb_path"),
        "smb_username": db.setting("smb_username"),
        "smb_password_set": bool(db.setting("smb_password")),
        "classify_share": db.setting("classify_share"),
        "classify_path": db.setting("classify_path"),
        "classify_root": db.setting("classify_root"),
        "classify_display": display_path(db.setting("classify_root")) if db.setting("classify_root") else "",
        "book_count": books,
        "folder_count": folders,
        "db_path": str(db.path),
        "readable": readable,
        "writable": readable and writable_dir(root),
        "root_display": display_path(root) if root else "",
    }


def _put_setting(conn, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def save_settings(db: Database, payload: dict) -> dict:
    previous = db.setting("library_root")
    previous_classify = db.setting("classify_root")
    smb_host = str(payload.get("smb_host") if "smb_host" in payload else "").strip()
    smb_share = str(payload.get("smb_share") or "").strip()
    smb_path = str(payload.get("smb_path") or "").strip().strip("/\\")
    smb_username = str(payload.get("smb_username") or "").strip()
    classify_share = str(payload.get("classify_share") or "").strip()
    classify_path = str(payload.get("classify_path") or "").strip().strip("/\\")
    smb_root = ""
    classify_root = ""
    if smb_host:
        from .smb import connect_and_prepare, parse_smb_location

        parsed_host, parsed_share, parsed_path = parse_smb_location(smb_host)
        if parsed_host:
            smb_host = parsed_host
        if parsed_share:
            smb_share = parsed_share
        if parsed_path:
            smb_path = parsed_path
        parsed_classify_host, parsed_classify_share, parsed_classify_path = parse_smb_location(classify_share)
        if parsed_classify_share:
            classify_share = parsed_classify_share
            if parsed_classify_path:
                classify_path = parsed_classify_path
        password = payload.get("smb_password")
        if not password:
            password = db.setting("smb_password")
        smb_root = connect_and_prepare(smb_host, smb_share, smb_path, smb_username, password or "")
        if classify_share:
            classify_host = parsed_classify_host or smb_host
            classify_root = connect_and_prepare(
                classify_host, classify_share, classify_path, smb_username, password or ""
            )
    with db.transaction() as conn:
        if smb_host:
            _put_setting(conn, "smb_host", smb_host)
            _put_setting(conn, "smb_share", smb_share)
            _put_setting(conn, "smb_path", smb_path)
            _put_setting(conn, "smb_username", smb_username)
            if payload.get("smb_password"):
                _put_setting(conn, "smb_password", str(payload["smb_password"]))
            root = smb_root
            if root != previous or (previous_classify and classify_root != previous_classify):
                clear_index(conn)
                _put_setting(conn, "last_scan_at", "")
            _put_setting(conn, "library_root", root)
            _put_setting(conn, "classify_share", classify_share)
            _put_setting(conn, "classify_path", classify_path)
            _put_setting(conn, "classify_root", classify_root)
        elif "library_root" in payload:
            root = str(payload["library_root"] or "").strip()
            if root:
                import os

                if not os.path.isdir(root):
                    raise ValueError("书库目录不存在")
                if not writable_dir(root):
                    raise ValueError("书库目录不可写")
            if root != previous:
                clear_index(conn)
                _put_setting(conn, "last_scan_at", "")
            _put_setting(conn, "library_root", root)
            if "smb_host" in payload:
                _put_setting(conn, "smb_host", "")
                _put_setting(conn, "smb_share", "")
                _put_setting(conn, "smb_path", "")
                _put_setting(conn, "smb_username", "")
                _put_setting(conn, "smb_password", "")
                _put_setting(conn, "classify_share", "")
                _put_setting(conn, "classify_path", "")
                _put_setting(conn, "classify_root", "")
        for key, field in (
            ("extensions", "extensions"),
            ("excludes", "excludes"),
            ("includes", "includes"),
            ("inbox", "inbox"),
        ):
            if field in payload:
                values = payload[field]
                if isinstance(values, str):
                    values = [line.strip() for line in values.splitlines() if line.strip()]
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(list(values), ensure_ascii=False)),
                )
        if "concurrency" in payload:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('concurrency', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(max(1, min(16, int(payload["concurrency"])))),),
            )
        if "batch_threshold" in payload:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('batch_threshold', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(max(1, int(payload["batch_threshold"]))),),
            )
    return library_status(db)


def ensure_share_node(conn, library_root: str, share: str) -> int:
    parent_id = root_folder(conn, library_root)
    row = conn.execute("SELECT * FROM folders WHERE relative_path=?", (share,)).fetchone()
    if row:
        conn.execute(
            "UPDATE folders SET parent_id=?, name=?, is_book=0, is_classified=1, missing=0 WHERE id=?",
            (parent_id, share, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        """
        INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen)
        VALUES (?, ?, ?, 0, 1, 0, 0)
        """,
        (share, parent_id, share),
    )
    return int(cur.lastrowid)


def _place(db: Database, relative: str) -> str:
    from .paths import placed_path

    return placed_path(
        db.setting("library_root"),
        db.setting("classify_root"),
        db.setting("classify_share"),
        relative,
    )


def root_folder(conn, root: str) -> int:
    row = conn.execute("SELECT id FROM folders WHERE relative_path=''").fetchone()
    if row:
        return row["id"]
    name = leaf_name(display_path(root).replace("\\", "/")) or "书库"
    cur = conn.execute(
        "INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen) VALUES ('', NULL, ?, 0, 0, 0, 0)",
        (name,),
    )
    return int(cur.lastrowid)


def list_folders(db: Database, parent_id: int | None, classify: bool = False) -> list[dict]:
    classify_share = ""
    if classify:
        _root, classify_share = classify_destination(db)
        if not classify_share:
            return []
    with db.transaction() as conn:
        if classify:
            row = conn.execute(
                "SELECT id FROM folders WHERE relative_path=? AND missing=0",
                (classify_share,),
            ).fetchone()
            if not row:
                ensure_share_node(conn, db.setting("library_root"), classify_share)
                row = conn.execute(
                    "SELECT id FROM folders WHERE relative_path=? AND missing=0",
                    (classify_share,),
                ).fetchone()
            if not row:
                return []
            parent_id = row["id"]
        elif parent_id is None:
            root = conn.execute("SELECT id FROM folders WHERE relative_path=''").fetchone()
            if not root:
                return []
            parent_id = root["id"]
        rows = conn.execute(
            """
            SELECT id, relative_path, name, is_classified
            FROM folders
            WHERE parent_id=? AND missing=0 AND is_book=0
            ORDER BY name COLLATE NOCASE
            """,
            (parent_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_folder(db: Database, parent_id: int | None, name: str) -> dict:
    error = name_error(name)
    if error:
        raise ValueError(error)
    root = db.setting("library_root")
    if not root:
        raise ValueError("尚未设置书库路径")
    classify = ""
    share = ""
    if parent_id is None:
        classify, share = classify_destination(db)
    ensure_connected(db)
    with db.transaction() as conn:
        if parent_id is None and classify and share:
            parent_id = ensure_share_node(conn, root, share)
        elif parent_id is None:
            parent_id = root_folder(conn, root)
        parent = conn.execute(
            "SELECT * FROM folders WHERE id=? AND missing=0 AND is_book=0",
            (parent_id,),
        ).fetchone()
        if not parent:
            raise ValueError("父目录不存在")
        relative = join_rel(parent["relative_path"], name)
        parent_abs = _place(db, parent["relative_path"])
        if name_taken(parent_abs, name):
            raise ValueError("同名文件夹已存在")
        import os

        os.mkdir(_place(db, relative))
        cur = conn.execute(
            """
            INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen)
            VALUES (?, ?, ?, 0, 1, 0, 0)
            """,
            (relative, parent_id, name),
        )
        folder_id = int(cur.lastrowid)
    return {"id": folder_id, "relative_path": relative, "name": name, "is_classified": 1}


def update_folder(db: Database, folder_id: int, payload: dict) -> dict:
    root = db.setting("library_root")
    ensure_connected(db)
    with db.transaction() as conn:
        folder = conn.execute("SELECT * FROM folders WHERE id=? AND missing=0", (folder_id,)).fetchone()
        if not folder or folder["is_book"]:
            raise ValueError("分类目录不存在")
        if folder["relative_path"] == "" and "name" in payload:
            raise ValueError("不能重命名书库根目录")
        share = db.setting("classify_share").strip()
        if share and folder["relative_path"] == share and payload.get("name"):
            raise ValueError("不能重命名分类共享根目录")
        if "is_classified" in payload:
            value = 1 if payload["is_classified"] else 0
            ids = [folder_id]
            if payload.get("cascade"):
                prefix = folder["relative_path"]
                rows = conn.execute(
                    """
                    SELECT id FROM folders
                    WHERE is_book=0 AND missing=0 AND (relative_path=? OR substr(relative_path, 1, ?)=?)
                    """,
                    (prefix, len(prefix) + 1, prefix + "/"),
                ).fetchall()
                ids = [row["id"] for row in rows]
            conn.executemany("UPDATE folders SET is_classified=? WHERE id=?", [(value, item) for item in ids])
        if payload.get("name") and payload["name"] != folder["name"]:
            error = name_error(payload["name"])
            if error:
                raise ValueError(error)
            parent = parent_rel(folder["relative_path"]) or ""
            parent_abs = _place(db, parent)
            if name_taken(parent_abs, payload["name"]):
                raise ValueError("同名文件夹已存在")
            new_rel = join_rel(parent, payload["name"])
            import os

            os.rename(_place(db, folder["relative_path"]), _place(db, new_rel))
            _move_prefix(conn, folder["relative_path"], new_rel)
            conn.execute("UPDATE folders SET name=? WHERE relative_path=?", (payload["name"], new_rel))
        row = conn.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
    return dict(row)


def delete_folder(db: Database, folder_id: int) -> None:
    root = db.setting("library_root")
    ensure_connected(db)
    with db.transaction() as conn:
        folder = conn.execute("SELECT * FROM folders WHERE id=? AND missing=0", (folder_id,)).fetchone()
        share = db.setting("classify_share").strip()
        if not folder or folder["is_book"] or folder["relative_path"] in {"", share}:
            raise ValueError("只能删除空的分类目录")
        child = conn.execute(
            "SELECT COUNT(*) AS n FROM folders WHERE parent_id=? AND missing=0",
            (folder_id,),
        ).fetchone()["n"]
        books = conn.execute(
            "SELECT COUNT(*) AS n FROM books WHERE folder_id=? AND missing=0",
            (folder_id,),
        ).fetchone()["n"]
        if child or books:
            raise ValueError("目录不为空")
        import os

        target = _place(db, folder["relative_path"])
        try:
            os.rmdir(target)
        except OSError as exc:
            raise ValueError("目录不为空或无法删除") from exc
        conn.execute("DELETE FROM folders WHERE id=?", (folder_id,))


def delete_books(db: Database, book_ids: list[int], on_progress=None, runner=None) -> dict:
    """删除所选图书文件夹，并清掉索引。同名只剩一本时取消标记。"""
    import os

    from .paths import _rmtree

    deleted = 0
    failed: list[dict] = []
    stopped = False
    total = len(book_ids)
    ensure_connected(db)

    def report(index: int, current: str = "") -> None:
        if on_progress:
            on_progress(
                {
                    "phase": "delete",
                    "done": index,
                    "total": total,
                    "deleted": deleted,
                    "failed": len(failed),
                    "current": current,
                }
            )

    for index, book_id in enumerate(book_ids, start=1):
        if runner is not None and runner.checkpoint() == "cancelled":
            stopped = True
            break
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT id, relative_path, folder_name FROM books WHERE id=? AND missing=0",
                (book_id,),
            ).fetchone()
        if not row or not str(row["relative_path"] or "").strip("/"):
            failed.append({"id": book_id, "error": "图书不存在"})
            report(index)
            continue
        relative = row["relative_path"]
        try:
            target = _place(db, relative)
        except ValueError as exc:
            failed.append({"id": book_id, "error": str(exc)})
            report(index, relative)
            continue
        if os.path.isdir(target):
            try:
                _rmtree(target)
            except OSError as exc:
                failed.append({"id": book_id, "error": f"无法删除文件夹：{exc}"})
                report(index, relative)
                continue
        with db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT id FROM books
                WHERE relative_path=? OR substr(relative_path, 1, ?)=?
                """,
                (relative, len(relative) + 1, relative + "/"),
            ).fetchall()
            for item in rows:
                conn.execute("DELETE FROM book_fts WHERE rowid=?", (item["id"],))
                conn.execute("DELETE FROM books WHERE id=?", (item["id"],))
            folders = conn.execute(
                """
                SELECT id, relative_path FROM folders
                WHERE relative_path=? OR substr(relative_path, 1, ?)=?
                """,
                (relative, len(relative) + 1, relative + "/"),
            ).fetchall()
            for folder in sorted(folders, key=lambda item: len(item["relative_path"]), reverse=True):
                conn.execute("DELETE FROM folders WHERE id=?", (folder["id"],))
            remaining = conn.execute(
                "SELECT id FROM books WHERE folder_name=? AND name_conflict=1 AND missing=0",
                (row["folder_name"],),
            ).fetchall()
            if len(remaining) == 1:
                conn.execute("UPDATE books SET name_conflict=0 WHERE id=?", (remaining[0]["id"],))
        cache = db.path.parent / "covers"
        name = f"shelf-{book_id}.jpg" if db.path.stem == "shelf" else f"{book_id}.jpg"
        try:
            (cache / name).unlink(missing_ok=True)
        except OSError:
            pass
        deleted += 1
        report(index, relative)
    return {"deleted": deleted, "failed": failed, "stopped": stopped}


def _move_prefix(conn, old: str, new: str) -> None:
    folders = conn.execute(
        """
        SELECT id, relative_path, name FROM folders
        WHERE relative_path=? OR substr(relative_path, 1, ?)=?
        """,
        (old, len(old) + 1, old + "/"),
    ).fetchall()
    for row in folders:
        updated = replace_prefix(row["relative_path"], old, new)
        if updated is None:
            continue
        conn.execute("UPDATE folders SET relative_path=? WHERE id=?", (updated, row["id"]))
    books = conn.execute(
        """
        SELECT id, relative_path, folder_name FROM books
        WHERE relative_path=? OR substr(relative_path, 1, ?)=?
        """,
        (old, len(old) + 1, old + "/"),
    ).fetchall()
    for row in books:
        updated = replace_prefix(row["relative_path"], old, new)
        if updated is None:
            continue
        conn.execute(
            "UPDATE books SET relative_path=?, folder_name=? WHERE id=?",
            (updated, leaf_name(updated), row["id"]),
        )
        refresh_fts(conn, row["id"])
    files = conn.execute("SELECT id, relative_path FROM book_files").fetchall()
    for row in files:
        updated = replace_prefix(row["relative_path"], old, new)
        if updated is not None:
            conn.execute("UPDATE book_files SET relative_path=? WHERE id=?", (updated, row["id"]))


_SEARCH_COLUMNS = (
    "b.title",
    "b.authors_text",
    "b.series",
    "b.tags_text",
    "b.isbn",
    "b.publisher",
    "b.folder_name",
    "b.relative_path",
    "b.suggested_category",
)


_CN_DIGITS = "零一二三四五六七八九"
_VOLUME_ORD = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十两]+)\s*([册卷部集篇])")
_VOLUME_SIDE = re.compile(r"(上|中|下)\s*[册卷部集篇]")
_VOLUME_PAREN = re.compile(r"[（(]\s*(上|中|下)\s*[）)]")
_VOLUME_SET = re.compile(r"上\s*中?\s*下\s*[册卷]")


def _cn_number(text: str) -> str:
    if text.isdigit():
        return str(int(text))
    if text == "十":
        return "10"
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.find(left) if left else 1
        ones = _CN_DIGITS.find(right) if right else 0
        if tens < 0 or ones < 0:
            return text
        return str(tens * 10 + ones)
    if len(text) == 1 and text in _CN_DIGITS:
        return str(_CN_DIGITS.find(text))
    return text


def _volume_mark(title: str) -> str:
    """从上册、下卷、第3册这类字样取出册次。套装上下册记成一整套。"""
    text = title or ""
    if _VOLUME_SET.search(text):
        return "全"
    ordinals = [
        f"{_cn_number(matched.group(1))}{matched.group(2)}"
        for matched in _VOLUME_ORD.finditer(text)
    ]
    if ordinals:
        return "".join(ordinals)
    matched = _VOLUME_SIDE.search(text) or _VOLUME_PAREN.search(text)
    return matched.group(1) if matched else ""


def _norm_series_index(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return text.casefold()
    if number == int(number):
        return str(int(number))
    return str(number)


def _split_distinct_parts(members: list) -> list[list]:
    """册次不同，或同一册次里丛书序号不同，就拆开。缺了标记的副本仍留在唯一的那一组里。"""
    by_volume: dict[str, list[tuple]] = {}
    for row in members:
        volume = _volume_mark(row["title"] or "")
        index = _norm_series_index(row["series_index"] or "")
        by_volume.setdefault(volume, []).append((row, index))
    known_volumes = [key for key in by_volume if key]
    chosen = {key: by_volume[key] for key in known_volumes} if len(known_volumes) > 1 else by_volume
    result: list[list] = []
    for rows in chosen.values():
        known_indexes = {index for _, index in rows if index}
        if len(known_indexes) <= 1:
            result.append([row for row, _ in rows])
            continue
        buckets: dict[str, list] = {}
        for row, index in rows:
            if index:
                buckets.setdefault(index, []).append(row)
        result.extend(buckets.values())
    return result


def _norm_title(value: str) -> str:
    text = (value or "").strip().casefold()
    for mark in ("《", "》", "「", "」", "『", "』"):
        text = text.replace(mark, "")
    return " ".join(text.split())


def _norm_isbn(value: str) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def list_duplicates(db: Database, page: int | None = None, page_size: int | None = None) -> dict:
    """书名相同，或 ISBN 相同，合成一组。丛书序号或上下册不同的分开。"""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT id, title, authors_text, primary_format, size, relative_path, isbn, folder_name,
                   series, series_index
            FROM books
            WHERE missing=0
            ORDER BY relative_path COLLATE NOCASE, id
            """
        ).fetchall()
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    seen_title: dict[str, int] = {}
    seen_isbn: dict[str, int] = {}
    for index, row in enumerate(rows):
        title = _norm_title(row["title"] or "")
        isbn = _norm_isbn(row["isbn"] or "")
        if title:
            previous = seen_title.get(title)
            if previous is None:
                seen_title[title] = index
            else:
                union(index, previous)
        if len(isbn) >= 10:
            previous = seen_isbn.get(isbn)
            if previous is None:
                seen_isbn[isbn] = index
            else:
                union(index, previous)

    grouped: dict[int, list] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(find(index), []).append(row)

    with db.transaction() as conn:
        ignored = {
            row["signature"]
            for row in conn.execute("SELECT signature FROM duplicate_ignores").fetchall()
        }

    groups = []
    for members in grouped.values():
        for part_members in _split_distinct_parts(members):
            if len(part_members) < 2:
                continue
            signature = ",".join(str(row["id"]) for row in sorted(part_members, key=lambda item: item["id"]))
            if signature in ignored:
                continue
            titles: list[str] = []
            for row in part_members:
                title = (row["title"] or row["folder_name"] or "未命名").strip() or "未命名"
                if title not in titles:
                    titles.append(title)
            same_title = len({_norm_title(title) for title in titles}) == 1
            label = min(titles, key=len) if same_title else " / ".join(titles[:3])
            groups.append(
                {
                    "label": label,
                    "count": len(part_members),
                    "signature": signature,
                    "books": [dict(row) for row in part_members],
                }
            )
    groups.sort(key=lambda item: (-item["count"], item["label"].casefold()))
    total_groups = len(groups)
    book_count = sum(item["count"] for item in groups)
    if page_size is None:
        shown = groups
        current = 1
        size = total_groups
    else:
        size = max(1, min(int(page_size), 100))
        pages = max(1, (total_groups + size - 1) // size)
        current = min(max(1, int(page or 1)), pages)
        start = (current - 1) * size
        shown = groups[start : start + size]
    return {
        "groups": shown,
        "group_count": total_groups,
        "book_count": book_count,
        "page": current,
        "page_size": size,
    }


def ignore_duplicate_group(db: Database, book_ids: list[int]) -> dict:
    """记下这一组的成员。成员不变时不再出现在重复列表，不删除文件。"""
    ids = sorted({int(book_id) for book_id in book_ids})
    if len(ids) < 2:
        raise ValueError("至少选择两本才能忽视")
    signature = ",".join(str(book_id) for book_id in ids)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO duplicate_ignores(signature, created_at) VALUES (?, ?) ON CONFLICT(signature) DO NOTHING",
            (signature, now()),
        )
    return {"ok": True, "signature": signature}


def refresh_suggestions(library: Database, shelf: Database | None = None, standard_path: Path | None = None) -> dict:
    """只给还没匹配过的书算一次推荐分类，结果写入 suggested_category。"""
    from pathlib import Path as PathType

    from .clc import suggest_clc

    del shelf
    source = standard_path if isinstance(standard_path, PathType) else None
    with library.transaction() as conn:
        rows = conn.execute(
            """
            SELECT id, title, tags_text
            FROM books
            WHERE missing=0 AND suggested_ready=0
            """
        ).fetchall()
        if not rows:
            return {"updated": 0, "unchanged": True}
        changes = []
        for row in rows:
            suggestion = suggest_clc(row["title"], row["tags_text"], source)
            changes.append((suggestion, int(row["id"])))
        conn.executemany(
            "UPDATE books SET suggested_category=?, suggested_ready=1 WHERE id=?",
            changes,
        )
    return {"updated": len(changes), "unchanged": False}


def _like_term(term: str) -> tuple[str, list]:
    """按原文片段匹配。三字以下的中文用全文索引会一条都搜不到。"""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    clause = "(" + " OR ".join(f"ifnull({column}, '') LIKE ? ESCAPE '\\'" for column in _SEARCH_COLUMNS) + ")"
    return clause, [pattern] * len(_SEARCH_COLUMNS)


def _filters(conn_unused, args: dict, config: dict) -> tuple[str, list]:
    clauses = ["b.missing=0"]
    params: list = []
    if args.get("folder_id"):
        if args.get("recursive"):
            row_path = args.get("_folder_path")
            if row_path:
                clauses.append("(b.relative_path=? OR substr(b.relative_path, 1, ?)=?)")
                params.extend([row_path, len(row_path) + 1, row_path + "/"])
        else:
            clauses.append("f.parent_id=?")
            params.append(args["folder_id"])
    if args.get("classified"):
        prefix = (config.get("classify_prefix") or "").strip("/")
        if prefix:
            clauses.append("(b.relative_path=? OR substr(b.relative_path, 1, ?)=?)")
            params.extend([prefix, len(prefix) + 1, prefix + "/"])
        else:
            clauses.append("ifnull(p.is_classified, 0)=1")
    if args.get("unclassified"):
        inbox_sql = ["ifnull(p.is_classified, 0)=0"]
        for prefix in config.get("inbox") or []:
            inbox_sql.append("(b.relative_path=? OR substr(b.relative_path, 1, ?)=?)")
            params.extend([prefix, len(prefix) + 1, prefix + "/"])
        clauses.append("(" + " OR ".join(inbox_sql) + ")")
    if args.get("tag"):
        clauses.append(
            "EXISTS (SELECT 1 FROM book_tags bt JOIN tags t ON t.id=bt.tag_id WHERE bt.book_id=b.id AND t.name=?)"
        )
        params.append(args["tag"])
    if args.get("author"):
        clauses.append(
            "EXISTS (SELECT 1 FROM book_authors ba JOIN authors a ON a.id=ba.author_id WHERE ba.book_id=b.id AND a.name=?)"
        )
        params.append(args["author"])
    if args.get("series"):
        clauses.append("b.series=?")
        params.append(args["series"])
    if args.get("language"):
        clauses.append("b.language=?")
        params.append(args["language"])
    if args.get("format"):
        clauses.append("b.primary_format=?")
        params.append(args["format"].lower().lstrip("."))
    if args.get("source"):
        clauses.append("b.metadata_source=?")
        params.append(args["source"])
    if args.get("suggest"):
        clauses.append("b.suggested_category=?")
        params.append(str(args["suggest"]))
    if args.get("path_prefix"):
        prefix = str(args["path_prefix"]).strip("/")
        clauses.append("(b.relative_path=? OR substr(b.relative_path, 1, ?)=?)")
        params.extend([prefix, len(prefix) + 1, prefix + "/"])
    if args.get("ids"):
        marks = ",".join("?" for _ in args["ids"])
        clauses.append(f"b.id IN ({marks})")
        params.extend(args["ids"])
    for term in str(args.get("q") or "").split():
        clause, extra = _like_term(term)
        clauses.append(clause)
        params.extend(extra)
    return " AND ".join(clauses), params


def _from_sql() -> str:
    return """
        FROM books b
        JOIN folders f ON f.id = b.folder_id
        LEFT JOIN folders p ON p.id = f.parent_id
    """


def _with_classify_prefix(db: Database, args: dict, config: dict) -> tuple[dict, dict]:
    config = dict(config)
    if args.get("classified"):
        share = (config.get("classify_share") or "").strip().strip("/")
        if not share and db.setting("smb_host").strip():
            share = "books"
        config["classify_prefix"] = share
    return args, config


def list_books(db: Database, args: dict) -> dict:
    from .metadata import suggest_title
    config = read_config(db)
    args, config = _with_classify_prefix(db, args, config)
    searching = bool(str(args.get("q") or "").strip())
    unpaged = searching or bool(str(args.get("suggest") or "").strip())
    page = max(1, int(args.get("page") or 1))
    page_size = min(100, max(1, int(args.get("page_size") or 40)))
    sort = SORTS.get(args.get("sort") or "name", SORTS["name"])
    order = "DESC" if str(args.get("order") or "").lower() == "desc" else "ASC"
    with db.transaction() as conn:
        if args.get("folder_id") and args.get("recursive"):
            folder = conn.execute("SELECT relative_path FROM folders WHERE id=?", (args["folder_id"],)).fetchone()
            args = dict(args)
            args["_folder_path"] = folder["relative_path"] if folder else ""
        where, params = _filters(conn, args, config)
        total = conn.execute(f"SELECT COUNT(*) AS n {_from_sql()} WHERE {where}", params).fetchone()["n"]
        limit_sql = "" if unpaged else "LIMIT ? OFFSET ?"
        limit_params = () if unpaged else (page_size, (page - 1) * page_size)
        rows = conn.execute(
            f"""
            SELECT b.id, b.title, b.authors_text, b.publisher, b.isbn, b.tags_text, b.series, b.series_index,
                   b.primary_format, b.relative_path, b.metadata_source, b.confidence,
                   b.language, b.mtime, b.size, b.name_conflict, b.suggested_category,
                   CASE WHEN ifnull(p.is_classified, 0)=0 THEN 1 ELSE 0 END AS parent_open
            {_from_sql()}
            WHERE {where}
            ORDER BY {sort} {order}, b.id
            {limit_sql}
            """,
            (*params, *limit_params),
        ).fetchall()
        if unpaged:
            page = 1
            page_size = total
    items = []
    inbox = config["inbox"]
    for row in rows:
        item = dict(row)
        unclassified = bool(item.pop("parent_open"))
        path = item["relative_path"]
        if any(path == prefix or path.startswith(prefix + "/") for prefix in inbox):
            unclassified = True
        item["unclassified"] = unclassified
        fix = suggest_title(item.get("title"))
        item["title_issue"] = fix["issue"]
        item["title_suggestion"] = fix["title"] if fix["issue"] else ""
        items.append(item)
    with db.transaction() as conn:
        suggestion_rows = conn.execute(
            """
            SELECT suggested_category AS name, COUNT(*) AS n
            FROM books
            WHERE missing=0 AND ifnull(suggested_category, '')!=''
            GROUP BY suggested_category
            ORDER BY n DESC, suggested_category
            """
        ).fetchall()
    suggestions = [{"name": row["name"], "count": row["n"]} for row in suggestion_rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "suggestions": suggestions}


def list_book_ids(db: Database, args: dict) -> dict:
    config = read_config(db)
    args, config = _with_classify_prefix(db, args, config)
    with db.transaction() as conn:
        if args.get("folder_id") and args.get("recursive"):
            folder = conn.execute("SELECT relative_path FROM folders WHERE id=?", (args["folder_id"],)).fetchone()
            args = dict(args)
            args["_folder_path"] = folder["relative_path"] if folder else ""
        where, params = _filters(conn, args, config)
        rows = conn.execute(f"SELECT b.id {_from_sql()} WHERE {where} ORDER BY b.id", params).fetchall()
    ids = [int(row["id"]) for row in rows]
    return {"ids": ids, "total": len(ids)}


def facets(db: Database, args: dict) -> dict:
    config = read_config(db)
    args, config = _with_classify_prefix(db, args, config)
    with db.transaction() as conn:
        if args.get("folder_id") and args.get("recursive"):
            folder = conn.execute("SELECT relative_path FROM folders WHERE id=?", (args["folder_id"],)).fetchone()
            args = dict(args)
            args["_folder_path"] = folder["relative_path"] if folder else ""
        where, params = _filters(conn, args, config)
        total = conn.execute(f"SELECT COUNT(*) AS n {_from_sql()} WHERE {where}", params).fetchone()["n"]

        def grouped(sql: str) -> list[dict]:
            rows = conn.execute(sql, params).fetchall()
            return [{"name": row["name"], "count": row["n"]} for row in rows]

        tags = grouped(
            f"""
            SELECT t.name AS name, COUNT(*) AS n
            {_from_sql()}
            JOIN book_tags bt ON bt.book_id=b.id
            JOIN tags t ON t.id=bt.tag_id
            WHERE {where}
            GROUP BY t.id ORDER BY n DESC, t.name LIMIT 40
            """
        )
        authors = grouped(
            f"""
            SELECT a.name AS name, COUNT(*) AS n
            {_from_sql()}
            JOIN book_authors ba ON ba.book_id=b.id
            JOIN authors a ON a.id=ba.author_id
            WHERE {where}
            GROUP BY a.id ORDER BY n DESC, a.name LIMIT 40
            """
        )
        formats = grouped(
            f"""
            SELECT b.primary_format AS name, COUNT(*) AS n
            {_from_sql()} WHERE {where} AND b.primary_format IS NOT NULL
            GROUP BY b.primary_format ORDER BY n DESC LIMIT 20
            """
        )
        languages = grouped(
            f"""
            SELECT b.language AS name, COUNT(*) AS n
            {_from_sql()} WHERE {where} AND b.language IS NOT NULL AND b.language != ''
            GROUP BY b.language ORDER BY n DESC LIMIT 20
            """
        )
    return {"total": total, "tags": tags, "authors": authors, "formats": formats, "languages": languages}


def get_book(db: Database, book_id: int) -> dict:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            raise ValueError("图书不存在")
        files = conn.execute(
            "SELECT relative_path, ext, size, mtime, is_primary FROM book_files WHERE book_id=? ORDER BY is_primary DESC, relative_path",
            (book_id,),
        ).fetchall()
        authors = conn.execute(
            "SELECT a.name FROM authors a JOIN book_authors ba ON ba.author_id=a.id WHERE ba.book_id=? ORDER BY a.name",
            (book_id,),
        ).fetchall()
        tags = conn.execute(
            "SELECT t.name FROM tags t JOIN book_tags bt ON bt.tag_id=t.id WHERE bt.book_id=? ORDER BY t.name",
            (book_id,),
        ).fetchall()
    data = dict(row)
    data["files"] = [dict(item) for item in files]
    data["authors"] = [item["name"] for item in authors]
    data["tags"] = [item["name"] for item in tags]
    return data


def _split_stored(text: str | None) -> list[str]:
    return [item for item in (text or "").split("、") if item]


def write_back_fields(db: Database, book_id: int, fields: dict) -> dict:
    """把图书详情里改过的字段写回该书的 metadata.opf。"""
    from .metadata import write_opf_fields

    if not fields:
        return {"written": 0, "failed": []}
    ensure_connected(db)
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT id, relative_path, title, authors_text, tags_text, missing
            FROM books WHERE id=?
            """,
            (book_id,),
        ).fetchone()
    if not row or row["missing"]:
        return {"written": 0, "failed": [{"id": book_id, "error": "图书不存在或已缺失"}]}
    try:
        folder = _place(db, row["relative_path"])
        if not os.path.isdir(folder):
            raise OSError("图书文件夹不存在")
        path = os.path.join(folder, "metadata.opf")
        payload = dict(fields)
        if not os.path.isfile(path):
            payload.setdefault("title", row["title"] or "")
            payload.setdefault("authors", _split_stored(row["authors_text"]))
            payload.setdefault("tags", _split_stored(row["tags_text"]))
        write_opf_fields(path, payload)
    except (OSError, ValueError) as exc:
        return {"written": 0, "failed": [{"id": book_id, "error": str(exc)}]}
    return {"written": 1, "failed": []}


def write_back_tags(db: Database, book_ids: list[int]) -> dict:
    """把索引里的标签写回每本书文件夹中的 metadata.opf。"""
    from .metadata import write_opf_tags

    ids = [int(item) for item in book_ids]
    if not ids:
        raise ValueError("请先选择图书")
    ensure_connected(db)
    marks = ",".join("?" for _ in ids)
    with db.transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT id, relative_path, title, authors_text, tags_text, missing
            FROM books WHERE id IN ({marks})
            """,
            ids,
        ).fetchall()
    found = {int(row["id"]): row for row in rows}
    written = 0
    failed: list[dict] = []
    for book_id in ids:
        row = found.get(book_id)
        if not row or row["missing"]:
            failed.append({"id": book_id, "error": "图书不存在或已缺失"})
            continue
        tags = [item for item in (row["tags_text"] or "").split("、") if item]
        authors = [item for item in (row["authors_text"] or "").split("、") if item]
        try:
            folder = _place(db, row["relative_path"])
            if not os.path.isdir(folder):
                raise OSError("图书文件夹不存在")
            write_opf_tags(os.path.join(folder, "metadata.opf"), tags, row["title"], authors)
        except (OSError, ValueError) as exc:
            failed.append({"id": book_id, "error": str(exc)})
            continue
        written += 1
    return {"written": written, "failed": failed}


def _clean_tag_names(tags: list[str]) -> list[str]:
    names: list[str] = []
    for tag in tags:
        text = (tag or "").strip()
        if text and text not in names:
            names.append(text)
    return names


def edit_book_tags(db: Database, book_ids: list[int], mode: str, tags: list[str]) -> dict:
    """增删或替换选中图书的标签。只有标签实际变化时才锁定并回写 metadata.opf。"""
    action = (mode or "").strip()
    if action not in {"add", "remove", "replace"}:
        raise ValueError("请选择添加、移除或替换")
    ids = [int(item) for item in book_ids]
    if not ids:
        raise ValueError("请先选择图书")
    incoming = _clean_tag_names(tags)
    if action != "remove" and not incoming:
        raise ValueError("请填写标签")
    marks = ",".join("?" for _ in ids)
    changed: list[int] = []
    failed: list[dict] = []
    with db.transaction() as conn:
        rows = conn.execute(
            f"SELECT id, tags_text, missing FROM books WHERE id IN ({marks})",
            ids,
        ).fetchall()
        found = {int(row["id"]): row for row in rows}
        for book_id in ids:
            row = found.get(book_id)
            if not row or row["missing"]:
                failed.append({"id": book_id, "error": "图书不存在或已缺失"})
                continue
            current = [item for item in (row["tags_text"] or "").split("、") if item]
            if action == "add":
                updated = current + [name for name in incoming if name not in current]
            elif action == "remove":
                drop = set(incoming)
                updated = [name for name in current if name not in drop]
            else:
                updated = list(incoming)
            if updated == current:
                continue
            set_tags(conn, book_id, updated)
            conn.execute("UPDATE books SET lock_tags=1 WHERE id=?", (book_id,))
            refresh_fts(conn, book_id)
            changed.append(book_id)
    report = write_back_tags(db, changed) if changed else {"written": 0, "failed": []}
    return {
        "updated": len(changed),
        "written": report["written"],
        "failed": failed + report["failed"],
        "unchanged": len(ids) - len(changed) - len(failed),
    }


_FOLDER_NAME_LIMIT = 80


def rename_marked_books(db: Database, book_ids: list[int]) -> dict:
    """把带《》或拼音的书名改干净，文件夹一并改名，并回写 metadata.opf。"""
    from .metadata import suggest_title

    ids = [int(item) for item in book_ids]
    if not ids:
        raise ValueError("请先选择图书")
    ensure_connected(db)
    renamed = 0
    written = 0
    failed: list[dict] = []
    for book_id in ids:
        try:
            outcome = _rename_marked_book(db, book_id, suggest_title)
        except (OSError, ValueError) as exc:
            failed.append({"id": book_id, "error": str(exc)})
            continue
        if outcome.get("skipped"):
            continue
        if outcome.get("error"):
            failed.append({"id": book_id, "error": outcome["error"]})
            continue
        if outcome.get("changed"):
            renamed += 1
        report = write_back_fields(db, book_id, outcome["fields"])
        if report["failed"]:
            failed.append({"id": book_id, "error": report["failed"][0]["error"]})
        else:
            written += report["written"]
    return {"renamed": renamed, "written": written, "failed": failed}


def _rename_marked_book(db: Database, book_id: int, suggest_title) -> dict:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=? AND missing=0", (book_id,)).fetchone()
        if not row:
            return {"error": "图书不存在或已缺失"}
        fix = suggest_title(row["title"])
        if not fix["issue"]:
            return {"skipped": True}
        new_title = (fix["title"] or "").strip()
        if not new_title:
            return {"error": "改后的书名为空"}
        folder_name = row["folder_name"] or ""
        new_folder = new_title if len(new_title) <= _FOLDER_NAME_LIMIT and not name_error(new_title) else ""
        rename_folder = bool(new_folder) and new_folder != folder_name
        relative = row["relative_path"]
        if rename_folder:
            parent = parent_rel(relative) or ""
            if name_taken(_place(db, parent), new_folder):
                return {"error": "同名文件夹已存在"}
            new_rel = join_rel(parent, new_folder)
            os.rename(_place(db, relative), _place(db, new_rel))
            _move_prefix(conn, relative, new_rel)
            conn.execute("UPDATE folders SET name=? WHERE relative_path=?", (new_folder, new_rel))
        changed = new_title != (row["title"] or "") or rename_folder or fix["authors"] is not None
        conn.execute("UPDATE books SET title=?, lock_title=1 WHERE id=?", (new_title, book_id))
        if fix["authors"] is not None:
            set_authors(conn, book_id, fix["authors"])
            conn.execute("UPDATE books SET lock_authors=1 WHERE id=?", (book_id,))
        refresh_fts(conn, book_id)
        tags = _split_stored(row["tags_text"])
    fields: dict = {"title": new_title}
    if fix["authors"] is not None:
        fields["authors"] = fix["authors"]
    if tags:
        fields["tags"] = tags
    return {"changed": changed, "fields": fields}


def update_book(db: Database, book_id: int, payload: dict) -> dict:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            raise ValueError("图书不存在")
        assignments: dict[str, Any] = {}
        if "title" in payload:
            assignments["title"] = (payload.get("title") or "").strip()
            assignments["lock_title"] = 1 if payload.get("lock_title", True) else 0
        elif "lock_title" in payload:
            assignments["lock_title"] = 1 if payload["lock_title"] else 0
        if "series" in payload or "series_index" in payload:
            if "series" in payload:
                assignments["series"] = (payload.get("series") or "").strip()
            if "series_index" in payload:
                assignments["series_index"] = str(payload.get("series_index") or "")
                assignments["series_index_num"] = series_num(assignments["series_index"])
            assignments["lock_series"] = 1 if payload.get("lock_series", True) else 0
        elif "lock_series" in payload:
            assignments["lock_series"] = 1 if payload["lock_series"] else 0
        for column in ("subtitle", "language", "publisher", "pubdate", "isbn", "description", "rating"):
            if column in payload:
                assignments[column] = payload.get(column) or ""
        if assignments:
            sets = ", ".join(f"{key}=?" for key in assignments)
            conn.execute(f"UPDATE books SET {sets} WHERE id=?", (*assignments.values(), book_id))
        if "authors" in payload:
            set_authors(conn, book_id, list(payload.get("authors") or []))
            lock = 1 if payload.get("lock_authors", True) else 0
            conn.execute("UPDATE books SET lock_authors=? WHERE id=?", (lock, book_id))
        elif "lock_authors" in payload:
            conn.execute(
                "UPDATE books SET lock_authors=? WHERE id=?",
                (1 if payload["lock_authors"] else 0, book_id),
            )
        if "tags" in payload:
            set_tags(conn, book_id, list(payload.get("tags") or []))
            lock = 1 if payload.get("lock_tags", True) else 0
            conn.execute("UPDATE books SET lock_tags=? WHERE id=?", (lock, book_id))
        elif "lock_tags" in payload:
            conn.execute(
                "UPDATE books SET lock_tags=? WHERE id=?",
                (1 if payload["lock_tags"] else 0, book_id),
            )
        refresh_fts(conn, book_id)
    changed = _changed_opf_fields(row, payload)
    result = get_book(db, book_id)
    if changed:
        report = write_back_fields(db, book_id, changed)
        if report["failed"]:
            result["opf_error"] = report["failed"][0]["error"]
        elif report["written"]:
            result["opf_written"] = True
            result["opf_fields"] = list(changed)
    return result


def _changed_opf_fields(row, payload: dict) -> dict:
    """只收集图书详情里真正改过、需要回写的字段。"""
    changed: dict = {}
    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if title != (row["title"] or ""):
            changed["title"] = title
    if "authors" in payload:
        authors = _clean_tag_names(list(payload.get("authors") or []))
        if authors != _split_stored(row["authors_text"]):
            changed["authors"] = authors
    if "tags" in payload:
        tags = _clean_tag_names(list(payload.get("tags") or []))
        if tags != _split_stored(row["tags_text"]):
            changed["tags"] = tags
    for column in ("publisher", "isbn", "series", "series_index"):
        if column not in payload:
            continue
        value = str(payload.get(column) or "").strip()
        if value != str(row[column] or "").strip():
            changed[column] = value
    return changed


def list_tags(db: Database) -> list[dict]:
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.name, COUNT(bt.book_id) AS count
            FROM tags t
            JOIN book_tags bt ON bt.tag_id=t.id
            JOIN books b ON b.id=bt.book_id AND b.missing=0
            GROUP BY t.id
            ORDER BY count DESC, t.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def rename_tag(db: Database, tag_id: int, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("标签名无效")
    with db.transaction() as conn:
        current = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        if not current:
            raise ValueError("标签不存在")
        other = conn.execute("SELECT id FROM tags WHERE name=? AND id<>?", (name, tag_id)).fetchone()
        if other:
            raise ValueError("已有同名标签，请改用合并")
        conn.execute("UPDATE tags SET name=? WHERE id=?", (name, tag_id))
        books = conn.execute("SELECT book_id FROM book_tags WHERE tag_id=?", (tag_id,)).fetchall()
        for row in books:
            _rewrite_text(conn, row["book_id"])
            conn.execute("UPDATE books SET lock_tags=1 WHERE id=?", (row["book_id"],))
        book_ids = [int(row["book_id"]) for row in books]
    report = write_back_tags(db, book_ids) if book_ids else {"written": 0, "failed": []}
    return {"id": tag_id, "name": name, "written": report["written"], "failed": report["failed"]}


def merge_tags(db: Database, source_id: int, target_id: int) -> dict:
    if source_id == target_id:
        raise ValueError("不能把标签合并到自己")
    with db.transaction() as conn:
        source = conn.execute("SELECT * FROM tags WHERE id=?", (source_id,)).fetchone()
        target = conn.execute("SELECT * FROM tags WHERE id=?", (target_id,)).fetchone()
        if not source or not target:
            raise ValueError("标签不存在")
        links = conn.execute("SELECT book_id FROM book_tags WHERE tag_id=?", (source_id,)).fetchall()
        for row in links:
            exists = conn.execute(
                "SELECT 1 FROM book_tags WHERE book_id=? AND tag_id=?",
                (row["book_id"], target_id),
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM book_tags WHERE book_id=? AND tag_id=?",
                    (row["book_id"], source_id),
                )
            else:
                conn.execute(
                    "UPDATE book_tags SET tag_id=? WHERE book_id=? AND tag_id=?",
                    (target_id, row["book_id"], source_id),
                )
            conn.execute("UPDATE books SET lock_tags=1 WHERE id=?", (row["book_id"],))
            _rewrite_text(conn, row["book_id"])
        conn.execute("DELETE FROM tags WHERE id=?", (source_id,))
        book_ids = [int(row["book_id"]) for row in links]
    report = write_back_tags(db, book_ids) if book_ids else {"written": 0, "failed": []}
    return {"id": target_id, "name": target["name"], "written": report["written"], "failed": report["failed"]}


def outermost(paths: list[str]) -> list[str]:
    kept: list[str] = []
    for path in sorted(set(paths), key=len):
        if any(path == item or path.startswith(item + "/") for item in kept):
            continue
        kept.append(path)
    return kept


def book_paths(conn, book_ids: list[int]) -> list[str]:
    if not book_ids:
        return []
    marks = ",".join("?" for _ in book_ids)
    rows = conn.execute(
        f"SELECT relative_path FROM books WHERE id IN ({marks}) AND missing=0",
        book_ids,
    ).fetchall()
    return [row["relative_path"] for row in rows]


def folder_paths(conn, folder_ids: list[int]) -> list[str]:
    if not folder_ids:
        return []
    marks = ",".join("?" for _ in folder_ids)
    rows = conn.execute(
        f"SELECT relative_path FROM folders WHERE id IN ({marks}) AND missing=0 AND is_book=0 AND relative_path<>''",
        folder_ids,
    ).fetchall()
    return [row["relative_path"] for row in rows]
