"""后台扫描、移动和删除。这些任务并行，各自暂停、继续和取消。"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from .catalog import book_paths, folder_paths, outermost, read_config
from .db import Database, now
from .moves import execute_moves
from .rules import apply_rules
from .scan import scan_stores


class JobRunner:
    def __init__(self, manager: "JobManager", job_id: int):
        self.manager = manager
        self.job_id = job_id
        self.message = ""
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self._gate = threading.Event()
        self._gate.set()

    def checkpoint(self) -> str:
        if self._cancel.is_set():
            return "cancelled"
        if self._pause.is_set():
            self.manager.mark(self.job_id, "paused", self.message or "已暂停")
            self._gate.wait()
            if self._cancel.is_set():
                return "cancelled"
            self.manager.mark(self.job_id, "running", "")
        return "running"

    def pause(self, message: str = "已暂停") -> None:
        self.message = message
        self._pause.set()
        self._gate.clear()
        self.manager.mark(self.job_id, "paused", message)

    def resume(self) -> None:
        self.message = ""
        self._pause.clear()
        self._gate.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._gate.set()


class JobManager:
    def __init__(self, db: Database, shelf: Database | None = None):
        self.db = db
        self.shelf = shelf
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.workers: dict[int, tuple[threading.Thread, JobRunner]] = {}
        self.listeners: list[asyncio.Queue] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self._last_progress = 0.0

    def _reap(self) -> None:
        dead = [job_id for job_id, (thread, _) in self.workers.items() if not thread.is_alive()]
        for job_id in dead:
            self.workers.pop(job_id, None)

    def busy(self) -> bool:
        with self.lock:
            self._reap()
            return bool(self.workers)

    def shutdown(self) -> None:
        with self.lock:
            workers = list(self.workers.values())
        for thread, runner in workers:
            runner.cancel()
        for thread, _ in workers:
            if thread.is_alive():
                thread.join(timeout=3)

    def add_listener(self, queue: asyncio.Queue) -> None:
        self.listeners.append(queue)

    def remove_listener(self, queue: asyncio.Queue) -> None:
        if queue in self.listeners:
            self.listeners.remove(queue)

    def publish(self, payload: dict) -> None:
        loop = self.loop
        if not loop:
            return
        for queue in list(self.listeners):
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    def mark(self, job_id: int, status: str, message: str = "", progress: dict | None = None) -> None:
        with self.db.transaction() as conn:
            if progress is None:
                conn.execute(
                    "UPDATE jobs SET status=?, message=?, updated_at=? WHERE id=?",
                    (status, message, now(), job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status=?, message=?, progress_json=?, updated_at=? WHERE id=?",
                    (status, message, json.dumps(progress, ensure_ascii=False), now(), job_id),
                )
        self.publish({"job": self.get(job_id)})

    def get(self, job_id: int) -> dict | None:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["progress"] = json.loads(item.pop("progress_json") or "{}")
        except json.JSONDecodeError:
            item["progress"] = {}
        return item

    def list_jobs(self, page: int = 1, page_size: int = 10) -> dict:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 10), 1), 100)
        with self.db.transaction() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
            rows = conn.execute(
                """
                SELECT id FROM jobs
                ORDER BY CASE WHEN status IN ('queued', 'running', 'paused') THEN 0 ELSE 1 END,
                         id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, (page - 1) * page_size),
            ).fetchall()
        items = [job for job in (self.get(row["id"]) for row in rows) if job]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    def _start(self, kind: str, target) -> dict:
        with self.lock:
            self._reap()
            with self.db.transaction() as conn:
                cur = conn.execute(
                    "INSERT INTO jobs(type, status, progress_json, message, created_at, updated_at) VALUES (?, 'queued', '{}', '', ?, ?)",
                    (kind, now(), now()),
                )
                job_id = int(cur.lastrowid)
            runner = JobRunner(self, job_id)

            def run(job_id: int, runner: JobRunner) -> None:
                try:
                    target(job_id, runner)
                finally:
                    with self.lock:
                        self.workers.pop(job_id, None)

            thread = threading.Thread(target=run, args=(job_id, runner), name=f"bm-{kind}-{job_id}", daemon=True)
            self.workers[job_id] = (thread, runner)
            self.thread = thread
            thread.start()
        return self.get(job_id)

    def start_scan(self) -> dict:
        return self._start("scan", self._scan)

    def start_move(self, sources: list[str], target: str, conflict: str, store: str = "library") -> dict:
        return self._start("move", lambda job_id, runner: self._move(job_id, runner, sources, target, conflict, store))

    def start_delete(self, book_ids: list[int], store: str = "library") -> dict:
        return self._start("delete", lambda job_id, runner: self._delete(job_id, runner, book_ids, store))

    def start_rules(self, scope: str, book_ids: list[int] | None) -> dict:
        return self._start("rules", lambda job_id, runner: self._rules(job_id, runner, scope, book_ids))

    def start_replace(
        self,
        book_ids: list[int],
        find: str,
        replace: str,
        field: str,
        finds: list[str] | None,
        store: str = "library",
    ) -> dict:
        return self._start(
            "replace",
            lambda job_id, runner: self._replace(job_id, runner, book_ids, find, replace, field, finds, store),
        )

    def _runner(self, job_id: int) -> JobRunner:
        with self.lock:
            item = self.workers.get(job_id)
        if not item or not item[0].is_alive():
            raise RuntimeError("这个任务没有在运行")
        return item[1]

    def pause(self, job_id: int) -> None:
        self._runner(job_id).pause("已暂停")

    def resume(self, job_id: int) -> None:
        self._runner(job_id).resume()

    def cancel(self, job_id: int) -> None:
        self._runner(job_id).cancel()

    def _scan(self, job_id: int, runner: JobRunner) -> None:
        log_path = self._log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.mark(job_id, "running", "正在扫描")
        with log_path.open("a", encoding="utf-8") as log:
            def on_error(path: str, message: str) -> None:
                log.write(f"{path}\t{message}\n")
                log.flush()

            def on_progress(progress: dict, force: bool = False) -> None:
                self.mark(job_id, "running" if not runner._pause.is_set() else "paused", "", progress)

            try:
                if self.db.setting("smb_host"):
                    self.mark(job_id, "running", "正在连接 SMB")
                from .catalog import ensure_connected

                ensure_connected(self.db)
                self.mark(job_id, "running", "正在扫描")
                status = scan_stores(self.db, self.shelf or self.db, read_config(self.db), runner, on_progress, on_error)
            except Exception as exc:
                self.mark(job_id, "failed", str(exc))
                return
        if status == "cancelled":
            self.mark(job_id, "cancelled", "已取消")
        else:
            self.mark(job_id, "completed", "扫描完成")

    def _folder_exists(self, db: Database, relative: str) -> bool:
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM folders WHERE relative_path=? AND missing=0 AND is_book=0",
                (relative,),
            ).fetchone()
        return row is not None

    def _execute_move(self, store, sources, target, conflict, job_id, runner, on_progress):
        if store == "shelf" and self.shelf is not None:
            from .catalog import sync_shelf

            sync_shelf(self.db, self.shelf)
            return execute_moves(self.shelf, sources, target, conflict, job_id, runner, on_progress)
        if self.shelf is not None and target and self._folder_exists(self.shelf, target):
            # 书库里也可能有同名文件夹。未分类的移动目标来自分类库，仍要搬到 books。
            return execute_moves(
                self.db, sources, target, conflict, job_id, runner, on_progress, shelf=self.shelf, cross=True
            )
        return execute_moves(self.db, sources, target, conflict, job_id, runner, on_progress)

    def _move(self, job_id: int, runner: JobRunner, sources: list[str], target: str, conflict: str, store: str = "library") -> None:
        self.mark(job_id, "running", "正在移动")

        def on_progress(progress: dict) -> None:
            status = "paused" if runner._pause.is_set() else "running"
            done = int(progress.get("done") or 0)
            total = int(progress.get("total") or 0)
            self.mark(job_id, status, f"{done}/{total}" if total else "", progress)

        try:
            result = self._execute_move(store, sources, target, conflict, job_id, runner, on_progress)
        except Exception as exc:
            self.mark(job_id, "failed", str(exc))
            return
        if runner._cancel.is_set():
            self.mark(job_id, "cancelled", result["summary"])
        elif result["failed"] and not result["moved"] and not result.get("marked"):
            self.mark(job_id, "failed", result["summary"])
        else:
            self.mark(job_id, "completed", result["summary"])

    def _delete(self, job_id: int, runner: JobRunner, book_ids: list[int], store: str = "library") -> None:
        from .catalog import delete_books, sync_shelf

        target_db = self.db
        if store == "shelf" and self.shelf is not None:
            sync_shelf(self.db, self.shelf)
            target_db = self.shelf
        total = len(book_ids)
        self.mark(job_id, "running", f"0/{total}", {"phase": "delete", "done": 0, "total": total, "deleted": 0, "failed": 0})

        def on_progress(progress: dict) -> None:
            status = "paused" if runner._pause.is_set() else "running"
            done = int(progress.get("done") or 0)
            whole = int(progress.get("total") or total)
            self.mark(job_id, status, f"{done}/{whole}" if whole else "", progress)

        try:
            result = delete_books(target_db, book_ids, on_progress=on_progress, runner=runner)
        except Exception as exc:
            self.mark(job_id, "failed", str(exc))
            return
        summary = f"已删除 {result['deleted']}，失败 {len(result['failed'])}"
        final = {
            "phase": "delete",
            "done": result["deleted"] + len(result["failed"]),
            "total": total,
            "deleted": result["deleted"],
            "failed": len(result["failed"]),
        }
        if runner._cancel.is_set() or result.get("stopped"):
            self.mark(job_id, "cancelled", summary, final)
        elif result["failed"] and not result["deleted"]:
            self.mark(job_id, "failed", summary, final)
        else:
            self.mark(job_id, "completed", summary, final)

    def _replace(
        self,
        job_id: int,
        runner: JobRunner,
        book_ids: list[int],
        find: str,
        replace: str,
        field: str,
        finds: list[str] | None,
        store: str = "library",
    ) -> None:
        from .catalog import replace_book_titles, sync_shelf

        target_db = self.db
        if store == "shelf" and self.shelf is not None:
            sync_shelf(self.db, self.shelf)
            target_db = self.shelf
        total = len(book_ids)
        self.mark(
            job_id,
            "running",
            f"0/{total}",
            {"phase": "replace", "done": 0, "total": total, "updated": 0, "written": 0, "failed": 0},
        )

        def on_progress(progress: dict) -> None:
            status = "paused" if runner._pause.is_set() else "running"
            done = int(progress.get("done") or 0)
            whole = int(progress.get("total") or total)
            self.mark(job_id, status, f"{done}/{whole}" if whole else "", progress)

        try:
            result = replace_book_titles(
                target_db,
                book_ids,
                find,
                replace,
                field,
                finds,
                on_progress=on_progress,
                runner=runner,
            )
        except Exception as exc:
            self.mark(job_id, "failed", str(exc))
            return
        summary = f"已替换 {result['updated']}，回写 {result['written']}，失败 {len(result['failed'])}"
        final = {
            "phase": "replace",
            "done": result["updated"] + result["unchanged"] + len(result["failed"]),
            "total": total,
            "updated": result["updated"],
            "written": result["written"],
            "failed": len(result["failed"]),
            "unchanged": result["unchanged"],
            "errors": result["failed"],
        }
        if runner._cancel.is_set() or result.get("stopped"):
            self.mark(job_id, "cancelled", summary, final)
        elif result["failed"] and not result["updated"]:
            self.mark(job_id, "failed", summary, final)
        else:
            self.mark(job_id, "completed", summary, final)

    def _rules(self, job_id: int, runner: JobRunner, scope: str, book_ids: list[int] | None) -> None:
        self.mark(job_id, "running", "正在按规则移动")
        try:
            result = apply_rules(self.db, scope, book_ids, job_id, runner)
        except Exception as exc:
            self.mark(job_id, "failed", str(exc))
            return
        message = f"计划 {result['planned']}，成功 {result['moved']}，跳过 {result['skipped']}，失败 {result['failed']}"
        self.mark(job_id, "completed", message, {"planned": result["planned"], "moved": result["moved"]})

    def _log_path(self, job_id: int) -> Path:
        return self.db.path.parent / "logs" / f"job-{job_id}.log"


def resolve_sources(db: Database, book_ids: list[int], folder_ids: list[int]) -> list[str]:
    with db.transaction() as conn:
        paths = book_paths(conn, book_ids) + folder_paths(conn, folder_ids)
    if not paths:
        raise ValueError("没有可移动的项目")
    return outermost(paths)
