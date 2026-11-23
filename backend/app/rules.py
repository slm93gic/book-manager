"""按标签等条件生成移动计划。扫描结束不会自动移动。"""

from __future__ import annotations

import json

from .catalog import book_paths, outermost
from .db import Database
from .moves import execute_moves
from .paths import name_error, parent_rel, split_rel


def _conditions(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("规则条件无效")
    cleaned = {}
    for key in ("tags", "authors", "formats"):
        values = raw.get(key) or []
        if isinstance(values, str):
            values = [part.strip() for part in values.split(",") if part.strip()]
        values = [str(item).strip() for item in values if str(item).strip()]
        if values:
            cleaned[key] = values
    for key in ("series", "language", "path_prefix"):
        value = str(raw.get(key) or "").strip()
        if value:
            cleaned[key] = value.strip("/") if key == "path_prefix" else value
    if not cleaned:
        raise ValueError("至少填写一个条件")
    return cleaned


def list_rules(db: Database) -> list[dict]:
    with db.transaction() as conn:
        rows = conn.execute("SELECT * FROM rules ORDER BY priority, id").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["conditions"] = json.loads(item.pop("conditions_json"))
        result.append(item)
    return result


def save_rule(db: Database, payload: dict, rule_id: int | None = None) -> dict:
    conditions = _conditions(payload.get("conditions") or {})
    target = str(payload.get("target") or "").strip().strip("/")
    split_rel(target)
    if not target:
        raise ValueError("请填写目标分类目录")
    leaf = target.split("/")[-1]
    error = name_error(leaf)
    if error:
        raise ValueError(error)
    name = (payload.get("name") or target).strip()
    create_missing = 1 if payload.get("create_missing") else 0
    enabled = 0 if payload.get("enabled") is False else 1
    with db.transaction() as conn:
        if rule_id is None:
            current = conn.execute("SELECT COALESCE(MAX(priority), 0) AS n FROM rules").fetchone()["n"]
            priority = int(payload.get("priority") or (current + 1))
            cur = conn.execute(
                """
                INSERT INTO rules(priority, name, conditions_json, target_relative_path, create_missing, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (priority, name, json.dumps(conditions, ensure_ascii=False), target, create_missing, enabled),
            )
            rule_id = int(cur.lastrowid)
        else:
            exists = conn.execute("SELECT id FROM rules WHERE id=?", (rule_id,)).fetchone()
            if not exists:
                raise ValueError("规则不存在")
            conn.execute(
                """
                UPDATE rules
                SET name=?, conditions_json=?, target_relative_path=?, create_missing=?, enabled=?
                WHERE id=?
                """,
                (name, json.dumps(conditions, ensure_ascii=False), target, create_missing, enabled, rule_id),
            )
    return next(item for item in list_rules(db) if item["id"] == rule_id)


def delete_rule(db: Database, rule_id: int) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))


def reorder_rules(db: Database, ids: list[int]) -> list[dict]:
    with db.transaction() as conn:
        for index, rule_id in enumerate(ids, start=1):
            conn.execute("UPDATE rules SET priority=? WHERE id=?", (index, rule_id))
    return list_rules(db)


def draft_from_tag(db: Database, tag_id: int) -> dict:
    with db.transaction() as conn:
        tag = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
    if not tag:
        raise ValueError("标签不存在")
    error = name_error(tag["name"])
    if error:
        raise ValueError(f"标签不能作为目录名：{error}")
    return save_rule(
        db,
        {
            "name": f"标签 {tag['name']}",
            "target": tag["name"],
            "create_missing": True,
            "enabled": True,
            "conditions": {"tags": [tag["name"]]},
        },
    )


def _matches(book: dict, authors: set[str], tags: set[str], cond: dict) -> bool:
    if cond.get("tags") and not set(cond["tags"]).issubset(tags):
        return False
    if cond.get("authors") and not set(cond["authors"]).issubset(authors):
        return False
    if cond.get("series") and (book["series"] or "") != cond["series"]:
        return False
    if cond.get("language") and (book["language"] or "") != cond["language"]:
        return False
    if cond.get("formats"):
        formats = {item.lower().lstrip(".") for item in cond["formats"]}
        if (book["primary_format"] or "") not in formats:
            return False
    prefix = cond.get("path_prefix") or ""
    if prefix and book["relative_path"] != prefix and not book["relative_path"].startswith(prefix + "/"):
        return False
    return True


def _load_books(db: Database, scope: str, book_ids: list[int] | None) -> list[dict]:
    from .catalog import read_config

    config = read_config(db)
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.title, b.series, b.language, b.primary_format, b.relative_path,
                   ifnull(p.is_classified, 0) AS parent_classified
            FROM books b
            JOIN folders f ON f.id = b.folder_id
            LEFT JOIN folders p ON p.id = f.parent_id
            WHERE b.missing=0
            """
        ).fetchall()
        authors: dict[int, set[str]] = {}
        tags: dict[int, set[str]] = {}
        for row in conn.execute(
            "SELECT ba.book_id, a.name FROM book_authors ba JOIN authors a ON a.id=ba.author_id"
        ):
            authors.setdefault(row["book_id"], set()).add(row["name"])
        for row in conn.execute("SELECT bt.book_id, t.name FROM book_tags bt JOIN tags t ON t.id=bt.tag_id"):
            tags.setdefault(row["book_id"], set()).add(row["name"])
    chosen = set(book_ids or [])
    inbox = config["inbox"]
    books = []
    for row in rows:
        if scope == "selection" and row["id"] not in chosen:
            continue
        path = row["relative_path"]
        unclassified = row["parent_classified"] == 0 or any(
            path == prefix or path.startswith(prefix + "/") for prefix in inbox
        )
        if scope == "unclassified" and not unclassified:
            continue
        item = dict(row)
        item["authors"] = authors.get(row["id"], set())
        item["tags"] = tags.get(row["id"], set())
        books.append(item)
    return books


def plan_rules(db: Database, scope: str, book_ids: list[int] | None = None) -> dict:
    if scope not in {"selection", "unclassified"}:
        raise ValueError("范围只能是当前选择或未分类")
    if scope == "selection" and not book_ids:
        raise ValueError("没有选择图书")
    rules = [item for item in list_rules(db) if item["enabled"]]
    books = _load_books(db, scope, book_ids)
    claimed: set[int] = set()
    groups = []
    total = 0
    for rule in rules:
        matched = []
        for book in books:
            if book["id"] in claimed:
                continue
            parent = parent_rel(book["relative_path"]) or ""
            if parent == rule["target_relative_path"]:
                continue
            if _matches(book, book["authors"], book["tags"], rule["conditions"]):
                matched.append(book)
        for book in matched:
            claimed.add(book["id"])
        if not matched:
            continue
        paths = outermost([book["relative_path"] for book in matched])
        groups.append(
            {
                "rule_id": rule["id"],
                "name": rule["name"],
                "target": rule["target_relative_path"],
                "create_missing": bool(rule["create_missing"]),
                "count": len(matched),
                "samples": [book["title"] or book["relative_path"] for book in matched[:8]],
                "paths": paths,
            }
        )
        total += len(matched)
    return {"groups": groups, "total": total}


def apply_rules(db: Database, scope: str, book_ids: list[int] | None, job_id: int | None, runner) -> dict:
    planned = plan_rules(db, scope, book_ids)
    root = db.setting("library_root")
    from .moves import ensure_category

    moved = skipped = failed = 0
    for group in planned["groups"]:
        if group["create_missing"]:
            with db.transaction() as conn:
                ensure_category(
                    conn,
                    root,
                    group["target"],
                    create=True,
                    classify_root=db.setting("classify_root"),
                    classify_share=db.setting("classify_share"),
                )
        result = execute_moves(
            db,
            group["paths"],
            group["target"],
            "skip",
            job_id=job_id,
            runner=runner,
        )
        moved += result["moved"]
        skipped += result.get("marked", 0)
        failed += result["failed"]
        if result["stopped"]:
            break
    return {"planned": planned["total"], "moved": moved, "skipped": skipped, "failed": failed, "groups": planned["groups"]}
