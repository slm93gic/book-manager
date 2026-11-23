"""三阶段扫描：走目录、读 OPF、解析电子书内部信息。"""

from __future__ import annotations

import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from .catalog import apply_parsed, refresh_fts, root_folder, set_authors, set_tags
from .db import Database, now
from .metadata import clean_text, head_hash, merge_meta, parse_book_folder, parse_opf_file, parse_primary
from .paths import (
    FORMAT_RANK,
    confined,
    excluded_name,
    from_long,
    included_book,
    io_kind,
    join_rel,
    link_escapes,
    list_children,
)


class Runner:
    def checkpoint(self) -> str:
        return "running"

    def pause(self, message: str = "") -> None:
        return None


def _extensions(config: dict) -> set[str]:
    return {item.lower().lstrip(".") for item in config["extensions"]}


def _is_book(files, extensions: set[str]) -> bool:
    for entry in files:
        if entry.name.lower() == "metadata.opf":
            return True
        ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
        if ext in extensions:
            return True
    return False


def _choose_primary(files, extensions: set[str]):
    candidates = []
    for entry in files:
        ext = os.path.splitext(entry.name)[1].lower()
        if ext.lstrip(".") in extensions:
            candidates.append((FORMAT_RANK.get(ext, 99), entry.name.lower(), entry))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _needs_body(meta: dict | None) -> bool:
    if not meta:
        return True
    return not all(meta.get(key) for key in ("title", "authors", "publisher", "isbn"))


def _parse_task(task: dict) -> dict:
    result = {"id": task["id"], "hash": None, "meta": None, "error": None}
    try:
        if task.get("primary_path"):
            result["hash"] = head_hash(task["primary_path"])
        opf_meta = parse_opf_file(task["opf_path"]) if task.get("opf_path") else None
        body_meta = None
        if task.get("primary_path") and _needs_body(opf_meta):
            body_meta = parse_primary(task["primary_path"], task.get("ext") or "")
        result["meta"] = merge_meta(opf_meta, body_meta)
    except Exception as exc:  # 单本失败不能打断整库扫描
        result["error"] = str(exc)
    return result


def scan_library(db: Database, config: dict, runner: Runner | None = None, on_progress=None, on_error=None) -> str:
    runner = runner or Runner()
    root = config["library_root"]
    if not root or not os.path.isdir(root):
        raise ValueError("书库目录不存在")
    extensions = _extensions(config)
    excludes = config["excludes"]
    includes = config["includes"]
    progress = {"phase": "walk", "dirs": 0, "books": 0, "parsed": 0, "failed": 0, "current": ""}

    def bump(force: bool = False) -> None:
        if on_progress:
            on_progress(dict(progress), force)

    with db.transaction() as conn:
        current = conn.execute("SELECT COALESCE(MAX(scan_gen), 0) AS n FROM folders").fetchone()["n"]
        gen = int(current) + 1
        conn.execute("UPDATE books SET seen=0")
        root_folder(conn, root)

    tasks: list[dict] = []
    buffer: list[dict] = []
    visited: set[str] = set()
    workers = max(1, int(config.get("concurrency") or 1))
    pool = ThreadPoolExecutor(max_workers=workers)
    inflight: dict = {}
    areas = [(root, "")]

    def apply_done(done: set) -> None:
        rows = []
        for future in done:
            task = inflight.pop(future, None)
            if task is None:
                continue
            try:
                result = future.result()
            except Exception as exc:
                result = {"id": task["id"], "hash": None, "meta": None, "error": str(exc)}
            rows.append(result)
        if not rows:
            return
        with db.transaction() as conn:
            for result in rows:
                if result.get("error"):
                    progress["failed"] += 1
                    if on_error:
                        on_error(str(result.get("id")), result["error"])
                if result.get("hash"):
                    conn.execute(
                        "UPDATE books SET head_hash=? WHERE id=?",
                        (result["hash"], result["id"]),
                    )
                if result.get("meta"):
                    apply_parsed(conn, result["id"], result["meta"])
                progress["parsed"] += 1
        bump(progress["parsed"] % 10 == 0)

    def drain(block: bool) -> str | None:
        if not inflight:
            return None
        if not block:
            done = {future for future in inflight if future.done()}
            if done:
                apply_done(done)
            return None
        pending = set(inflight)
        while pending:
            if runner.checkpoint() == "cancelled":
                return "cancelled"
            done, pending = wait(pending, timeout=0.4, return_when=FIRST_COMPLETED)
            if done:
                apply_done(done)
        return None

    def enqueue(new_tasks: list[dict]) -> None:
        for task in new_tasks:
            future = pool.submit(_parse_task, task)
            inflight[future] = task
    def flush() -> None:
        if not buffer:
            return
        before = len(tasks)
        with db.transaction() as conn:
            for item in buffer:
                _upsert(conn, item, gen, tasks)
        buffer.clear()
        enqueue(tasks[before:])
        drain(False)
        bump(True)

    try:
        for disk_root, prefix in areas:
            stack: list[tuple[str, bool]] = [("", False)]
            while stack:
                state = runner.checkpoint()
                if state == "cancelled":
                    flush()
                    return "cancelled"
                inner, inside_book = stack.pop()
                rel = join_rel(prefix, inner) if prefix else inner
                progress["dirs"] += 1
                progress["current"] = rel or "/"
                if progress["dirs"] % 25 == 0:
                    drain(False)
                    bump()
                try:
                    abs_path = confined(disk_root, inner)
                    real = os.path.normcase(os.path.realpath(from_long(abs_path)))
                    if real in visited:
                        continue
                    visited.add(real)
                    children = list_children(abs_path)
                except OSError as exc:
                    kind = io_kind(exc)
                    if kind == "断线":
                        stack.append((inner, inside_book))
                        progress["dirs"] -= 1
                        runner.pause("SMB 断开，已暂停，可继续")
                        continue
                    progress["failed"] += 1
                    if on_error:
                        on_error(rel or "/", f"{kind}: {exc}")
                    continue
                files = []
                subdirs = []
                for entry in children:
                    child_rel = join_rel(rel, entry.name)
                    if entry.is_dir:
                        if excluded_name(entry.name, excludes):
                            continue
                        if entry.is_link and link_escapes(disk_root, entry.path):
                            progress["failed"] += 1
                            if on_error:
                                on_error(child_rel, "符号链接越出书库")
                            continue
                        subdirs.append(entry)
                    elif entry.is_link and link_escapes(disk_root, entry.path):
                        continue
                    else:
                        files.append(entry)
                detected = _is_book(files, extensions) and inner != ""
                if detected and not included_book(rel, includes):
                    detected = False
                opf_hash = ""
                if detected:
                    opf_entry = next((entry for entry in files if entry.name.lower() == "metadata.opf"), None)
                    if opf_entry:
                        opf_hash = head_hash(opf_entry.path) or ""
                    progress["books"] += 1
                if prefix and inner == "":
                    display_name = prefix
                elif rel:
                    display_name = rel.split("/")[-1]
                else:
                    display_name = os.path.basename(os.path.abspath(disk_root)) or "书库"
                if detected or not inside_book:
                    buffer.append(
                        {
                            "rel": rel,
                            "name": display_name,
                            "is_book": detected,
                            "files": files,
                            "abs_path": abs_path,
                            "opf_hash": opf_hash,
                        }
                    )
                if len(buffer) >= 80:
                    flush()
                child_inside = inside_book or detected
                for entry in sorted(subdirs, key=lambda item: item.name.lower(), reverse=True):
                    stack.append((join_rel(inner, entry.name), child_inside))
        flush()
        progress["phase"] = "parse"
        bump(True)
        if drain(True) == "cancelled":
            return "cancelled"

        with db.transaction() as conn:
            conn.execute("UPDATE books SET missing=1 WHERE seen=0")
            conn.execute("UPDATE folders SET missing=1 WHERE scan_gen<? AND relative_path<>''", (gen,))
            conn.execute("UPDATE folders SET missing=0, scan_gen=? WHERE relative_path=''", (gen,))
        progress["phase"] = "relink"
        bump(True)
        with db.transaction() as conn:
            _relink(conn, gen)
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES('last_scan_at', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (now(),),
            )
            progress["phase"] = "done"
            bump(True)
            return "completed"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def scan_stores(library: Database, shelf: Database, config: dict, runner: Runner | None = None, on_progress=None, on_error=None) -> str:
    """书库和 books 各扫进自己的库，缺失标记不会跨库。"""
    from .catalog import classify_destination, sync_shelf

    library_config = dict(config)
    library_config["classify_root"] = ""
    library_config["classify_share"] = ""
    status = scan_library(library, library_config, runner, on_progress, on_error)
    if status != "completed":
        return status
    try:
        root, _share = classify_destination(library)
    except ValueError:
        return status
    if not root or not os.path.isdir(root):
        return status
    sync_shelf(library, shelf)
    shelf_config = dict(library_config)
    shelf_config["library_root"] = root
    return scan_library(shelf, shelf_config, runner, on_progress, on_error)


def _upsert(conn, item: dict, gen: int, tasks: list[dict]) -> None:
    rel = item["rel"]
    parent = None if rel == "" else (rel.rsplit("/", 1)[0] if "/" in rel else "")
    parent_id = None
    if parent is not None:
        row = conn.execute("SELECT id FROM folders WHERE relative_path=?", (parent,)).fetchone()
        parent_id = row["id"] if row else None
    existing = conn.execute("SELECT id FROM folders WHERE relative_path=?", (rel,)).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE folders
            SET parent_id=?, name=?, is_book=?, missing=0, scan_gen=?
            WHERE id=?
            """,
            (parent_id, item["name"], 1 if item["is_book"] else 0, gen, existing["id"]),
        )
        folder_id = existing["id"]
    else:
        cur = conn.execute(
            """
            INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen)
            VALUES (?, ?, ?, ?, 0, 0, ?)
            """,
            (rel, parent_id, item["name"], 1 if item["is_book"] else 0, gen),
        )
        folder_id = int(cur.lastrowid)
    if not item["is_book"]:
        return
    files = item["files"]
    opf = next((entry for entry in files if entry.name.lower() == "metadata.opf"), None)
    ranked = []
    for entry in files:
        ext = os.path.splitext(entry.name)[1].lower()
        if ext in FORMAT_RANK:
            ranked.append((FORMAT_RANK[ext], entry.name.lower(), entry))
    ranked.sort()
    primary = ranked[0][2] if ranked else None
    total_size = sum(entry.size for entry in files)
    mtime = max((entry.mtime for entry in files), default=0)
    primary_size = primary.size if primary else 0
    primary_mtime = primary.mtime if primary else 0
    sig = f"{primary_size}:{primary_mtime}:{opf.size if opf else 0}:{opf.mtime if opf else 0}:{item.get('opf_hash') or ''}"
    primary_ext = os.path.splitext(primary.name)[1].lower() if primary else ""
    book = conn.execute("SELECT * FROM books WHERE relative_path=?", (rel,)).fetchone()
    if book:
        conn.execute(
            """
            UPDATE books
            SET folder_id=?, folder_name=?, size=?, mtime=?, file_sig=?, primary_format=?,
                missing=0, seen=1, scan_gen=?
            WHERE id=?
            """,
            (
                folder_id,
                item["name"],
                total_size,
                mtime,
                sig,
                primary_ext.lstrip("."),
                gen,
                book["id"],
            ),
        )
        book_id = book["id"]
        source = book["metadata_source"] or ""
        if not clean_text(book["title"]):
            source = "filename"
        skip = book["file_sig"] == sig and source not in ("", "filename") and book["head_hash"]
        parent_name = parent.rsplit("/", 1)[-1] if parent else ""
        if source in ("", "filename"):
            apply_parsed(conn, book_id, parse_book_folder(item["name"], parent_name))
    else:
        cur = conn.execute(
            """
            INSERT INTO books(
              folder_id, relative_path, folder_name, size, mtime, file_sig, primary_format,
              missing, seen, born_gen, scan_gen, metadata_source, confidence, title
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, 'filename', 'low', ?)
            """,
            (
                folder_id,
                rel,
                item["name"],
                total_size,
                mtime,
                sig,
                primary_ext.lstrip("."),
                gen,
                gen,
                item["name"],
            ),
        )
        book_id = int(cur.lastrowid)
        parent_name = parent.rsplit("/", 1)[-1] if parent else ""
        apply_parsed(conn, book_id, parse_book_folder(item["name"], parent_name))
        skip = False
    conn.execute("DELETE FROM book_files WHERE book_id=?", (book_id,))
    for entry in files:
        ext = os.path.splitext(entry.name)[1].lower()
        is_primary = primary is not None and entry.name == primary.name
        conn.execute(
            """
            INSERT INTO book_files(book_id, relative_path, ext, size, mtime, is_primary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (book_id, join_rel(rel, entry.name), ext, entry.size, entry.mtime, 1 if is_primary else 0),
        )
    if not skip:
        tasks.append(
            {
                "id": book_id,
                "opf_path": opf.path if opf else None,
                "primary_path": primary.path if primary else None,
                "ext": primary_ext,
                "sig": sig,
            }
        )


def _relink(conn, gen: int) -> None:
    missing = conn.execute(
        """
        SELECT * FROM books
        WHERE missing=1 AND head_hash IS NOT NULL AND head_hash != ''
        """
    ).fetchall()
    used: set[int] = set()
    for old in missing:
        rows = conn.execute(
            """
            SELECT * FROM books
            WHERE missing=0 AND born_gen=? AND head_hash=? AND ifnull(size, -1)=ifnull(?, -1)
              AND ifnull(title, '')=ifnull(?, '') AND ifnull(authors_text, '')=ifnull(?, '')
            """,
            (gen, old["head_hash"], old["size"], old["title"] or "", old["authors_text"] or ""),
        ).fetchall()
        rows = [row for row in rows if row["id"] not in used and row["id"] != old["id"]]
        if len(rows) != 1:
            continue
        new = rows[0]
        used.add(new["id"])
        if old["lock_title"]:
            conn.execute(
                "UPDATE books SET title=?, subtitle=?, lock_title=1 WHERE id=?",
                (old["title"], old["subtitle"], new["id"]),
            )
        if old["lock_series"]:
            conn.execute(
                """
                UPDATE books
                SET series=?, series_index=?, series_index_num=?, lock_series=1
                WHERE id=?
                """,
                (old["series"], old["series_index"], old["series_index_num"], new["id"]),
            )
        if old["lock_authors"]:
            names = conn.execute(
                """
                SELECT a.name FROM authors a
                JOIN book_authors ba ON ba.author_id=a.id
                WHERE ba.book_id=?
                """,
                (old["id"],),
            ).fetchall()
            set_authors(conn, new["id"], [row["name"] for row in names])
            conn.execute("UPDATE books SET lock_authors=1 WHERE id=?", (new["id"],))
        if old["lock_tags"]:
            names = conn.execute(
                """
                SELECT t.name FROM tags t
                JOIN book_tags bt ON bt.tag_id=t.id
                WHERE bt.book_id=?
                """,
                (old["id"],),
            ).fetchall()
            set_tags(conn, new["id"], [row["name"] for row in names])
            conn.execute("UPDATE books SET lock_tags=1 WHERE id=?", (new["id"],))
        refresh_fts(conn, new["id"])
        conn.execute("DELETE FROM book_fts WHERE rowid=?", (old["id"],))
        conn.execute("DELETE FROM books WHERE id=?", (old["id"],))
