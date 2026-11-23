"""网页 API。文件操作只走本机已映射的书库路径。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .catalog import (
    create_folder,
    delete_folder,
    clear_scan_records,
    split_mixed_index,
    sync_shelf,
    facets,
    get_book,
    library_status,
    list_book_ids,
    list_books,
    list_duplicates,
    ignore_duplicate_group,
    list_folders,
    list_tags,
    merge_tags,
    read_config,
    rename_tag,
    save_settings,
    edit_book_tags,
    rename_marked_books,
    update_book,
    update_folder,
    write_back_tags,
)
from .covers import cover_path
from .db import Database, now
from .jobs import JobManager, resolve_sources
from .moves import undo_operation
from .rules import apply_rules, delete_rule, draft_from_tag, list_rules, plan_rules, reorder_rules, save_rule

REPO = Path(__file__).resolve().parents[2]
PUBLIC_PATHS = {"/api/health", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}


class Core:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(data_dir / "book.db")
        self.shelf = Database(data_dir / "shelf.db")
        sync_shelf(self.db, self.shelf)
        split_mixed_index(self.db, self.shelf)
        self.jobs = JobManager(self.db, self.shelf)


def create_app(data_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.core.jobs.loop = asyncio.get_running_loop()
        with app.state.core.db.transaction() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status='interrupted', message='服务重启，任务已中断', updated_at=?
                WHERE status IN ('queued', 'running', 'paused')
                """,
                (now(),),
            )
        yield
        app.state.core.jobs.shutdown()

    app = FastAPI(title="图书分类", lifespan=lifespan)
    app.state.core = Core(data_dir or Path(__import__("os").environ.get("BOOK_MANAGER_DATA", REPO / "data")))

    def core(request: Request) -> Core:
        return request.app.state.core

    def active_db(request: Request) -> Database:
        selected = request.app.state.core
        if request.query_params.get("store") == "shelf":
            sync_shelf(selected.db, selected.shelf)
            return selected.shelf
        return selected.db

    def require_user(request: Request) -> None:
        if request.url.path in PUBLIC_PATHS:
            return
        token = _token_from(request)
        if not token:
            raise HTTPException(401, "未登录")
        with request.app.state.core.db.transaction() as conn:
            row = conn.execute("SELECT token FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(401, "未登录")

    private = []

    def guard(endpoint):
        private.append(endpoint)
        return endpoint

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path.startswith("/api") and request.url.path not in PUBLIC_PATHS:
            try:
                require_user(request)
            except HTTPException as exc:
                return Response(
                    content=json.dumps({"detail": exc.detail}, ensure_ascii=False),
                    status_code=exc.status_code,
                    media_type="application/json",
                )
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        db = request.app.state.core.db
        has_password = bool(db.setting("password_hash"))
        token = _token_from(request)
        logged_in = False
        if token:
            with db.transaction() as conn:
                logged_in = conn.execute("SELECT 1 FROM sessions WHERE token=?", (token,)).fetchone() is not None
        return {"has_password": has_password, "logged_in": logged_in}

    @app.post("/api/auth/setup")
    def setup(body: PasswordBody, response: Response, request: Request):
        db = request.app.state.core.db
        if db.setting("password_hash"):
            raise HTTPException(400, "口令已设置")
        _validate_password(body.password)
        _store_password(db, body.password)
        return {"token": _issue(db, response)}

    @app.post("/api/auth/login")
    def login(body: PasswordBody, response: Response, request: Request):
        db = request.app.state.core.db
        if not _check_password(db, body.password):
            raise HTTPException(401, "口令不正确")
        return {"token": _issue(db, response)}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = _token_from(request)
        if token:
            with request.app.state.core.db.transaction() as conn:
                conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        response.delete_cookie("bm_token", path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: PasswordChange, request: Request):
        db = request.app.state.core.db
        if not _check_password(db, body.old_password):
            raise HTTPException(400, "原口令不正确")
        _validate_password(body.password)
        _store_password(db, body.password)
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings(request: Request):
        return library_status(request.app.state.core.db)

    @app.put("/api/settings")
    def put_settings(body: SettingsBody, request: Request):
        try:
            return save_settings(request.app.state.core.db, body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/folders")
    def folders(request: Request, parent_id: int | None = None, classify: int = 0):
        db = active_db(request)
        try:
            items = list_folders(db, parent_id, classify=bool(classify) and db is request.app.state.core.db)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        payload = {"items": items}
        if parent_id is None:
            with db.transaction() as conn:
                if classify:
                    share = db.setting("classify_share").strip().strip("/")
                    row = (
                        conn.execute(
                            "SELECT id, relative_path, name, is_classified FROM folders WHERE relative_path=? AND missing=0",
                            (share,),
                        ).fetchone()
                        if share
                        else None
                    )
                else:
                    row = conn.execute(
                        "SELECT id, relative_path, name, is_classified FROM folders WHERE relative_path=''"
                    ).fetchone()
            payload["root"] = dict(row) if row else None
        return payload

    @app.post("/api/folders")
    def add_folder(body: FolderCreate, request: Request):
        try:
            return create_folder(active_db(request), body.parent_id, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"无法新建文件夹：{exc}") from exc

    @app.patch("/api/folders/{folder_id}")
    def patch_folder(folder_id: int, body: FolderPatch, request: Request):
        try:
            return update_folder(active_db(request), folder_id, body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/folders/{folder_id}")
    def remove_folder(folder_id: int, request: Request):
        try:
            delete_folder(active_db(request), folder_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/books")
    def books(request: Request):
        args = dict(request.query_params)
        if args.get("folder_id"):
            args["folder_id"] = int(args["folder_id"])
        if args.get("page"):
            args["page"] = int(args["page"])
        if args.get("page_size"):
            args["page_size"] = int(args["page_size"])
        args["recursive"] = args.get("recursive") in {"1", "true", "True"}
        args["unclassified"] = args.get("unclassified") in {"1", "true", "True"}
        args["classified"] = args.get("classified") in {"1", "true", "True"}
        return list_books(active_db(request), args)

    @app.get("/api/books/ids")
    def book_ids(request: Request):
        args = dict(request.query_params)
        if args.get("folder_id"):
            args["folder_id"] = int(args["folder_id"])
        args["recursive"] = args.get("recursive") in {"1", "true", "True"}
        args["unclassified"] = args.get("unclassified") in {"1", "true", "True"}
        args["classified"] = args.get("classified") in {"1", "true", "True"}
        return list_book_ids(active_db(request), args)

    @app.get("/api/books/facets")
    def book_facets(request: Request):
        args = dict(request.query_params)
        if args.get("folder_id"):
            args["folder_id"] = int(args["folder_id"])
        args["recursive"] = args.get("recursive") in {"1", "true", "True"}
        args["unclassified"] = args.get("unclassified") in {"1", "true", "True"}
        args["classified"] = args.get("classified") in {"1", "true", "True"}
        return facets(active_db(request), args)

    @app.get("/api/books/duplicates")
    def book_duplicates(request: Request):
        return list_duplicates(active_db(request))

    @app.post("/api/books/duplicates/ignore")
    def ignore_duplicates(body: DeleteBooksBody, request: Request):
        try:
            return ignore_duplicate_group(active_db(request), body.book_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/books/{book_id}")
    def book_detail(book_id: int, request: Request):
        try:
            return get_book(active_db(request), book_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/books/delete")
    def remove_books(body: DeleteBooksBody, request: Request):
        if not body.book_ids:
            raise HTTPException(400, "没有可删除的图书")
        try:
            return request.app.state.core.jobs.start_delete(
                body.book_ids,
                request.query_params.get("store") or "library",
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/books/write-tags")
    def write_tags(body: WriteTagsBody, request: Request):
        try:
            return write_back_tags(active_db(request), body.book_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/books/rename-titles")
    def rename_titles(body: DeleteBooksBody, request: Request):
        if request.app.state.core.jobs.busy():
            raise HTTPException(409, "有任务正在运行，请先在任务页暂停或取消对应任务再重命名")
        try:
            return rename_marked_books(active_db(request), body.book_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/books/tags")
    def edit_tags(body: EditTagsBody, request: Request):
        try:
            return edit_book_tags(active_db(request), body.book_ids, body.mode, body.tags)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/books/{book_id}")
    def patch_book(book_id: int, body: BookPatch, request: Request):
        try:
            return update_book(active_db(request), book_id, body.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/covers/{book_id}")
    def cover(book_id: int, request: Request):
        path = cover_path(active_db(request), book_id)
        if not path:
            raise HTTPException(404, "没有封面")
        return FileResponse(path, media_type="image/jpeg")

    @app.post("/api/moves")
    def moves(body: MoveBody, request: Request):
        db = active_db(request)
        config = read_config(request.app.state.core.db)
        if body.folder_ids and not body.confirm_category:
            raise HTTPException(
                409,
                detail={"need_category_confirm": True, "message": "移动分类目录会带走其中全部图书"},
            )
        try:
            sources = resolve_sources(db, body.book_ids, body.folder_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if len(sources) > config["batch_threshold"] and not body.confirmed:
            raise HTTPException(
                409,
                detail={
                    "need_confirm": True,
                    "count": len(sources),
                    "target": body.target,
                    "threshold": config["batch_threshold"],
                },
            )
        try:
            return request.app.state.core.jobs.start_move(
                sources,
                body.target.strip().strip("/"),
                body.conflict,
                request.query_params.get("store") or "library",
            )
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/tags")
    def tags(request: Request):
        return {"items": list_tags(request.app.state.core.db)}

    @app.post("/api/tags/rename")
    def tag_rename(body: TagRename, request: Request):
        try:
            return rename_tag(request.app.state.core.db, body.tag_id, body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tags/merge")
    def tag_merge(body: TagMerge, request: Request):
        try:
            return merge_tags(request.app.state.core.db, body.source_id, body.target_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tags/{tag_id}/draft-rule")
    def tag_rule(tag_id: int, request: Request):
        try:
            return draft_from_tag(request.app.state.core.db, tag_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/rules")
    def rules(request: Request):
        return {"items": list_rules(request.app.state.core.db)}

    @app.post("/api/rules")
    def add_rule(body: RuleBody, request: Request):
        try:
            return save_rule(request.app.state.core.db, body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/rules/{rule_id}")
    def edit_rule(rule_id: int, body: RuleBody, request: Request):
        try:
            return save_rule(request.app.state.core.db, body.model_dump(), rule_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/rules/{rule_id}")
    def remove_rule(rule_id: int, request: Request):
        delete_rule(request.app.state.core.db, rule_id)
        return {"ok": True}

    @app.post("/api/rules/reorder")
    def reorder(body: ReorderBody, request: Request):
        return {"items": reorder_rules(request.app.state.core.db, body.ids)}

    @app.post("/api/rules/preview")
    def preview(body: RuleRun, request: Request):
        try:
            return plan_rules(request.app.state.core.db, body.scope, body.book_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/rules/apply")
    def run_rules(body: RuleRun, request: Request):
        try:
            planned = plan_rules(request.app.state.core.db, body.scope, body.book_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not body.confirmed:
            raise HTTPException(409, detail={"need_confirm": True, "preview": planned})
        try:
            job = request.app.state.core.jobs.start_rules(body.scope, body.book_ids)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        job["preview"] = planned
        return job

    @app.get("/api/jobs")
    def jobs(request: Request, page: int = 1, page_size: int = 10):
        return request.app.state.core.jobs.list_jobs(page, page_size)

    @app.get("/api/jobs/stream")
    async def job_stream(request: Request):
        queue: asyncio.Queue = asyncio.Queue()
        manager = request.app.state.core.jobs
        manager.add_listener(queue)

        async def events():
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                manager.remove_listener(queue)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: int, request: Request):
        job = request.app.state.core.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return job

    @app.get("/api/jobs/{job_id}/errors")
    def job_errors(job_id: int, request: Request):
        path = request.app.state.core.jobs._log_path(job_id)
        if not path.exists():
            return Response("没有失败记录\n", media_type="text/plain; charset=utf-8")
        return FileResponse(path, media_type="text/plain; charset=utf-8", filename=path.name)

    @app.post("/api/jobs/scan")
    def start_scan(request: Request):
        try:
            return request.app.state.core.jobs.start_scan()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/jobs/{job_id}/pause")
    def pause_job(job_id: int, request: Request):
        try:
            request.app.state.core.jobs.pause(job_id)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: int, request: Request):
        try:
            request.app.state.core.jobs.resume(job_id)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/scan/clear")
    def clear_scan(request: Request):
        if request.app.state.core.jobs.busy():
            raise HTTPException(409, "有任务正在运行，请先取消对应任务再清空")
        return clear_scan_records(active_db(request))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, request: Request):
        try:
            request.app.state.core.jobs.cancel(job_id)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.get("/api/operations")
    def operations(request: Request):
        db = request.app.state.core.db
        with db.transaction() as conn:
            rows = conn.execute("SELECT * FROM operations ORDER BY id DESC LIMIT 50").fetchall()
            items = []
            for row in rows:
                item = dict(row)
                counts = conn.execute(
                    """
                    SELECT status, COUNT(*) AS n FROM operation_items
                    WHERE operation_id=? GROUP BY status
                    """,
                    (row["id"],),
                ).fetchall()
                item["counts"] = {count["status"]: count["n"] for count in counts}
                items.append(item)
        return {"items": items}

    @app.get("/api/operations/{operation_id}")
    def operation_detail(operation_id: int, request: Request):
        db = request.app.state.core.db
        with db.transaction() as conn:
            row = conn.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if not row:
                raise HTTPException(404, "操作不存在")
            children = conn.execute(
                "SELECT * FROM operation_items WHERE operation_id=? ORDER BY id",
                (operation_id,),
            ).fetchall()
        return {"operation": dict(row), "items": [dict(item) for item in children]}

    @app.post("/api/operations/{operation_id}/undo")
    def undo(operation_id: int, request: Request):
        if request.app.state.core.jobs.busy():
            raise HTTPException(409, "有任务正在运行")
        try:
            return undo_operation(request.app.state.core.db, operation_id, request.app.state.core.shelf)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    dist = REPO / "frontend" / "dist"
    if dist.exists():

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


class PasswordBody(BaseModel):
    password: str


class PasswordChange(BaseModel):
    old_password: str
    password: str


class SettingsBody(BaseModel):
    library_root: str | None = None
    smb_host: str | None = None
    smb_share: str | None = None
    smb_path: str | None = None
    smb_username: str | None = None
    smb_password: str | None = None
    classify_share: str | None = None
    classify_path: str | None = None
    extensions: list[str] | str | None = None
    excludes: list[str] | str | None = None
    includes: list[str] | str | None = None
    inbox: list[str] | str | None = None
    concurrency: int | None = None
    batch_threshold: int | None = None


class FolderCreate(BaseModel):
    parent_id: int | None = None
    name: str


class FolderPatch(BaseModel):
    name: str | None = None
    is_classified: bool | None = None
    cascade: bool | None = None


class BookPatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    series: str | None = None
    series_index: str | None = None
    language: str | None = None
    publisher: str | None = None
    pubdate: str | None = None
    isbn: str | None = None
    description: str | None = None
    rating: str | None = None
    authors: list[str] | None = None
    tags: list[str] | None = None
    lock_title: bool | None = None
    lock_authors: bool | None = None
    lock_tags: bool | None = None
    lock_series: bool | None = None


class WriteTagsBody(BaseModel):
    book_ids: list[int] = Field(default_factory=list)


class DeleteBooksBody(BaseModel):
    book_ids: list[int] = Field(default_factory=list)


class EditTagsBody(BaseModel):
    book_ids: list[int] = Field(default_factory=list)
    mode: str = "add"
    tags: list[str] = Field(default_factory=list)


class MoveBody(BaseModel):
    book_ids: list[int] = Field(default_factory=list)
    folder_ids: list[int] = Field(default_factory=list)
    target: str = ""
    conflict: str = "skip"
    confirmed: bool = False
    confirm_category: bool = False


class TagRename(BaseModel):
    tag_id: int
    name: str


class TagMerge(BaseModel):
    source_id: int
    target_id: int


class RuleBody(BaseModel):
    name: str = ""
    target: str
    conditions: dict
    create_missing: bool = False
    enabled: bool = True


class ReorderBody(BaseModel):
    ids: list[int]


class RuleRun(BaseModel):
    scope: str
    book_ids: list[int] = Field(default_factory=list)
    confirmed: bool = False


def _token_from(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("bm_token") or request.query_params.get("token") or ""


def _validate_password(password: str) -> None:
    if len(password or "") < 4:
        raise HTTPException(400, "口令至少 4 位")


def _store_password(db: Database, password: str) -> None:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    db.set_setting("password_salt", salt)
    db.set_setting("password_hash", digest)


def _check_password(db: Database, password: str) -> bool:
    salt = db.setting("password_salt")
    expected = db.setting("password_hash")
    if not expected or not salt:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return secrets.compare_digest(digest, expected)


def _issue(db: Database, response: Response) -> str:
    token = secrets.token_urlsafe(32)
    with db.transaction() as conn:
        conn.execute("INSERT INTO sessions(token, created_at) VALUES (?, ?)", (token, now()))
    response.set_cookie("bm_token", token, httponly=True, samesite="lax", path="/")
    return token


app = create_app()
