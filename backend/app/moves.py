"""把图书文件夹移动到分类目录，并记录可撤销的操作。"""

from __future__ import annotations

import os

from .catalog import _move_prefix, _place, ensure_share_node, root_folder, sync_shelf, transfer_tree
from .db import Database, now
from .paths import confined, io_kind, join_rel, leaf_name, name_taken, relocate_directory


class Runner:
    def checkpoint(self) -> str:
        return "running"


def _signature(conn, db: Database, relative: str) -> str:
    book = conn.execute("SELECT id FROM books WHERE relative_path=?", (relative,)).fetchone()
    if book:
        primary = conn.execute(
            "SELECT relative_path FROM book_files WHERE book_id=? AND is_primary=1",
            (book["id"],),
        ).fetchone()
        if primary:
            try:
                stat = os.stat(_place(db, primary["relative_path"]))
                return f"file:{int(stat.st_size)}:{int(stat.st_mtime)}"
            except OSError as exc:
                return "err:" + io_kind(exc)
    try:
        names = []
        with os.scandir(_place(db, relative)) as it:
            names.extend(entry.name for entry in it)
        return "dir:" + "|".join(sorted(names))
    except OSError as exc:
        return "err:" + io_kind(exc)


def _same_sig(conn, db: Database, relative: str, sig: str | None) -> bool:
    if not sig:
        return True
    return _signature(conn, db, relative) == sig


def ensure_category(conn, root: str, relative: str, create: bool, classify_root: str = "", classify_share: str = "") -> int:
    relative = (relative or "").strip("/")
    share = (classify_share or "").strip().strip("/")
    on_classify = bool(classify_root and share and (relative == share or relative.startswith(share + "/")))
    if on_classify:
        parent_id = ensure_share_node(conn, root, share)
        inner = "" if relative == share else relative[len(share) + 1 :]
        disk = classify_root
        index = share
    else:
        parent_id = root_folder(conn, root)
        inner = relative
        disk = root
        index = ""
    if not os.path.isdir(disk):
        raise ValueError("书库目录不存在")
    if not inner:
        return parent_id
    disk_rel = ""
    for part in inner.split("/"):
        index = join_rel(index, part)
        disk_rel = join_rel(disk_rel, part)
        row = conn.execute("SELECT * FROM folders WHERE relative_path=?", (index,)).fetchone()
        if row and row["is_book"] and not row["missing"]:
            raise ValueError("目标路径落在图书文件夹内")
        abs_path = confined(disk, disk_rel)
        if not os.path.isdir(abs_path):
            if not create:
                raise ValueError("目标目录不存在")
            os.mkdir(abs_path)
        if row:
            conn.execute(
                "UPDATE folders SET missing=0, is_book=0, parent_id=?, name=? WHERE id=?",
                (parent_id, part, row["id"]),
            )
            parent_id = row["id"]
            continue
        cur = conn.execute(
            """
            INSERT INTO folders(relative_path, parent_id, name, is_book, is_classified, missing, scan_gen)
            VALUES (?, ?, ?, 0, 1, 0, 0)
            """,
            (index, parent_id, part),
        )
        parent_id = int(cur.lastrowid)
    return parent_id


def execute_moves(
    db: Database,
    sources: list[str],
    target: str,
    conflict: str,
    job_id: int | None = None,
    runner: Runner | None = None,
    on_progress=None,
    shelf: Database | None = None,
    cross: bool = False,
) -> dict:
    runner = runner or Runner()
    root = db.setting("library_root")
    if not root:
        raise ValueError("尚未设置书库路径")
    from .catalog import ensure_connected

    ensure_connected(db)
    classify_root = db.setting("classify_root")
    classify_share = db.setting("classify_share")
    target = (target or "").strip("/")
    conflict = conflict if conflict in {"skip", "suffix"} else "skip"
    kind = "move"
    if cross:
        if shelf is None:
            raise ValueError("尚未准备分类书库")
        sync_shelf(db, shelf)
        root = shelf.setting("library_root")
        if not root:
            raise ValueError("尚未设置分类目录 books")
        classify_root = db.setting("classify_root") or root
        kind = "move-shelf"
    with (shelf if cross and shelf is not None else db).transaction() as conn:
        target_id = ensure_category(
            conn,
            root,
            target,
            create=False,
            classify_root="" if cross else classify_root,
            classify_share="" if cross else classify_share,
        )
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO operations(job_id, kind, summary, created_at, undone) VALUES (?, ?, ?, ?, 0)",
            (job_id, kind, f"{len(sources)} 项 → {target or '书库根'}", now()),
        )
        operation_id = int(cur.lastrowid)
    moved = skipped = marked = failed = 0
    stopped = False
    for index, src in enumerate(sources, start=1):
        state = runner.checkpoint()
        if state == "cancelled":
            stopped = True
            break
        while True:
            state = runner.checkpoint()
            if state == "cancelled":
                stopped = True
                break
            if cross and shelf is not None:
                result = _move_across(db, shelf, src, target, target_id, conflict, operation_id, classify_root)
            else:
                result = _move_one(db, src, target, target_id, conflict, operation_id, classify_root)
            if result["status"] == "retry":
                runner_pause = getattr(runner, "pause", None)
                if runner_pause:
                    runner_pause("SMB 断开，已暂停")
                    continue
                result = {"status": "failed", "error": "断线", "src": src, "dst": src}
            break
        if stopped:
            break
        if result["status"] == "success":
            moved += 1
        elif result["status"] == "skipped":
            skipped += 1
        elif result["status"] == "marked":
            marked += 1
        else:
            failed += 1
            if result.get("error") == "跨文件系统" or str(result.get("error") or "").startswith("书库与分类共享"):
                stopped = True
        if on_progress:
            on_progress(
                {
                    "phase": "move",
                    "current": src,
                    "done": index,
                    "total": len(sources),
                    "moved": moved,
                    "skipped": skipped,
                    "marked": marked,
                    "failed": failed,
                }
            )
        if stopped:
            break
    summary = f"成功 {moved}，同名 {marked}，失败 {failed}"
    with db.transaction() as conn:
        conn.execute("UPDATE operations SET summary=? WHERE id=?", (summary, operation_id))
    return {
        "operation_id": operation_id,
        "moved": moved,
        "skipped": skipped,
        "marked": marked,
        "failed": failed,
        "stopped": stopped,
        "summary": summary,
    }


def _move_one(db: Database, src: str, target: str, target_id: int, _conflict: str, operation_id: int, classify_root: str = "") -> dict:
    name = leaf_name(src)
    if not name:
        return _item(db, operation_id, src, src, "failed", "不能移动书库根", None)
    if target == src or target.startswith(src + "/"):
        return _item(db, operation_id, src, src, "failed", "不能移动到自身内部", None)
    try:
        parent_abs = _place(db, target)
        src_abs = _place(db, src)
    except ValueError as exc:
        return _item(db, operation_id, src, src, "failed", str(exc), None)
    if not os.path.isdir(src_abs):
        return _item(db, operation_id, src, src, "failed", "源目录不存在", None)
    dest = join_rel(target, name)
    if dest == src:
        return _item(db, operation_id, src, dest, "skipped", "已经在目标目录", None)
    if name_taken(parent_abs, name):
        return _mark_same_name(db, db, src, dest, operation_id)
    if dest.startswith(src + "/"):
        return _item(db, operation_id, src, dest, "failed", "不能移动到自身内部", None)
    try:
        relocate_directory(src_abs, _place(db, dest))
    except OSError as exc:
        kind = io_kind(exc)
        if kind == "断线":
            return {"status": "retry"}
        if kind == "跨文件系统" and classify_root:
            kind = "书库与分类共享不在同一文件系统，已停止，没有复制文件"
        return _item(db, operation_id, src, dest, "failed", kind, None)
    try:
        with db.transaction() as conn:
            _move_prefix(conn, src, dest)
            conn.execute("UPDATE folders SET parent_id=?, name=? WHERE relative_path=?", (target_id, name, dest))
            sig = _signature(conn, db, dest)
            _item_conn(conn, operation_id, src, dest, "success", None, sig)
    except Exception as exc:
        try:
            relocate_directory(_place(db, dest), src_abs)
        except OSError:
            pass
        return _item(db, operation_id, src, dest, "failed", str(exc), None)
    return {"status": "success", "src": src, "dst": dest}


def _move_across(
    library: Database,
    shelf: Database,
    src: str,
    target: str,
    target_id: int,
    _conflict: str,
    operation_id: int,
    classify_root: str,
) -> dict:
    """把书库里的一本搬到 books。两个共享在同一块磁盘上，文件在 NAS 上复制后删除源目录，索引从书库表移入分类表。"""
    name = leaf_name(src)
    if not name:
        return _item(library, operation_id, src, src, "failed", "不能移动书库根", None)
    try:
        parent_abs = _place(shelf, target)
        src_abs = _place(library, src)
    except ValueError as exc:
        return _item(library, operation_id, src, src, "failed", str(exc), None)
    if not os.path.isdir(src_abs):
        return _item(library, operation_id, src, src, "failed", "源目录不存在", None)
    if name_taken(parent_abs, name):
        return _mark_same_name(library, shelf, src, join_rel(target, name), operation_id)
    dest = join_rel(target, name)
    try:
        dest_abs = _place(shelf, dest)
        relocate_directory(src_abs, dest_abs)
    except OSError as exc:
        kind = io_kind(exc)
        if kind == "断线":
            return {"status": "retry"}
        if kind == "跨文件系统" and classify_root:
            kind = "书库与分类共享不在同一文件系统，已停止，没有复制文件"
        return _item(library, operation_id, src, dest, "failed", kind, None)
    try:
        transfer_tree(library, shelf, src, dest, target_id)
        with shelf.transaction() as conn:
            sig = _signature(conn, shelf, dest)
        _item(library, operation_id, src, dest, "success", None, sig)
    except Exception as exc:
        try:
            relocate_directory(dest_abs, src_abs)
        except OSError:
            pass
        return _item(library, operation_id, src, dest, "failed", str(exc), None)
    return {"status": "success", "src": src, "dst": dest}


def _mark_same_name(src_db: Database, dst_db: Database, src: str, dest: str, operation_id: int) -> dict:
    """同名时不跳过、不改名。两边都标出来，留给用户决定删哪一本。"""
    _flag_conflict(src_db, src)
    _flag_conflict(dst_db, dest)
    return _item(src_db, operation_id, src, dest, "marked", "同名", None)


def _flag_conflict(db: Database, relative: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE books SET name_conflict=1 WHERE relative_path=? AND missing=0",
            (relative,),
        )


def _item(db: Database, operation_id: int, src: str, dst: str, status: str, error: str | None, sig: str | None) -> dict:
    with db.transaction() as conn:
        _item_conn(conn, operation_id, src, dst, status, error, sig)
    return {"status": status, "error": error, "src": src, "dst": dst}


def _item_conn(conn, operation_id: int, src: str, dst: str, status: str, error: str | None, sig: str | None) -> None:
    conn.execute(
        """
        INSERT INTO operation_items(operation_id, src_path, dst_path, status, error, sig)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (operation_id, src, dst, status, error, sig),
    )


def undo_operation(db: Database, operation_id: int, shelf: Database | None = None) -> dict:
    from .catalog import ensure_connected

    ensure_connected(db)
    root = db.setting("library_root")
    with db.transaction() as conn:
        op = conn.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if not op:
            raise ValueError("操作不存在")
        if op["undone"]:
            raise ValueError("该操作已经撤销")
        items = conn.execute(
            """
            SELECT * FROM operation_items
            WHERE operation_id=? AND status='success'
            ORDER BY length(dst_path) DESC, id DESC
            """,
            (operation_id,),
        ).fetchall()
        op_kind = op["kind"]
    undone = 0
    failed = 0
    for item in items:
        src = item["src_path"]
        dst = item["dst_path"]
        across = op_kind == "move-shelf" and shelf is not None
        dst_db = shelf if across else db
        try:
            if across:
                sync_shelf(db, shelf)
            dst_abs = _place(dst_db, dst)
            src_abs = _place(db, src)
        except ValueError as exc:
            _mark_undo(db, item["id"], f"不可撤销：{exc}")
            failed += 1
            continue
        if not os.path.isdir(dst_abs) or os.path.exists(src_abs):
            _mark_undo(db, item["id"], "不可撤销：目录已变化")
            failed += 1
            continue
        with dst_db.transaction() as conn:
            same = _same_sig(conn, dst_db, dst, item["sig"])
        if not same:
            _mark_undo(db, item["id"], "不可撤销：文件已变化")
            failed += 1
            continue
        parent = src.rsplit("/", 1)[0] if "/" in src else ""
        try:
            if name_taken(_place(db, parent), leaf_name(src)):
                raise FileExistsError(src)
            relocate_directory(dst_abs, src_abs)
        except OSError as exc:
            _mark_undo(db, item["id"], f"不可撤销：{io_kind(exc)}")
            failed += 1
            continue
        try:
            if across:
                with db.transaction() as conn:
                    parent_id = ensure_category(conn, root, parent, create=False)
                transfer_tree(shelf, db, dst, src, parent_id)
                with db.transaction() as conn:
                    conn.execute("UPDATE operation_items SET status='undone', error=NULL WHERE id=?", (item["id"],))
            else:
                with db.transaction() as conn:
                    _move_prefix(conn, dst, src)
                    parent_id = ensure_category(
                        conn,
                        root,
                        parent,
                        create=False,
                        classify_root=db.setting("classify_root"),
                        classify_share=db.setting("classify_share"),
                    )
                    conn.execute(
                        "UPDATE folders SET parent_id=?, name=? WHERE relative_path=?",
                        (parent_id, leaf_name(src), src),
                    )
                    conn.execute("UPDATE operation_items SET status='undone', error=NULL WHERE id=?", (item["id"],))
            undone += 1
        except Exception as exc:
            try:
                relocate_directory(src_abs, dst_abs)
            except OSError:
                pass
            _mark_undo(db, item["id"], f"不可撤销：{exc}")
            failed += 1
    with db.transaction() as conn:
        if failed == 0:
            conn.execute("UPDATE operations SET undone=1 WHERE id=?", (operation_id,))
    return {"operation_id": operation_id, "undone": undone, "failed": failed}
