"""封面缩略图缓存在本机，不在扫描过程中生成。"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from .db import Database, json_list
from .paths import DEFAULT_EXTENSIONS, placed_path


def cover_path(db: Database, book_id: int) -> Path | None:
    with db.transaction() as conn:
        book = conn.execute("SELECT * FROM books WHERE id=? AND missing=0", (book_id,)).fetchone()
        if not book:
            return None
        files = conn.execute(
            "SELECT relative_path, ext, is_primary, mtime FROM book_files WHERE book_id=?",
            (book_id,),
        ).fetchall()
    root = db.setting("library_root")
    classify = db.setting("classify_root")
    share = db.setting("classify_share")
    if not root:
        return None
    extensions = {item.lower().lstrip(".") for item in json_list(db.setting("extensions"))} or set(DEFAULT_EXTENSIONS)
    source = _find_source(root, book["relative_path"], files, extensions, classify, share)
    if not source:
        return None
    name = f"shelf-{book_id}.jpg" if db.path.stem == "shelf" else f"{book_id}.jpg"
    cache = db.path.parent / "covers" / name
    failed = cache.with_suffix(".fail")
    try:
        source_mtime = os.path.getmtime(source[0])
    except OSError:
        return None
    if cache.exists() and cache.stat().st_mtime >= source_mtime:
        return cache
    if failed.exists() and failed.stat().st_mtime >= source_mtime:
        return None
    try:
        _write_thumb(source, cache)
    except Exception:
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_bytes(b"")
        return None
    failed.unlink(missing_ok=True)
    return cache if cache.exists() else None


_COVER_NAMES = {"cover.jpg", "cover.jpeg", "cover.png", "cover.webp"}


def _named_cover(directory: str) -> str | None:
    matches = []
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name.lower()
                if name in _COVER_NAMES:
                    matches.append((0 if name == "cover.jpg" else 1, entry.path))
    except OSError:
        return None
    if not matches:
        return None
    matches.sort()
    return matches[0][1]


def _directory_is_book(directory: str, extensions: set[str]) -> bool:
    try:
        with os.scandir(directory) as scan:
            for entry in scan:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.name.lower() == "metadata.opf":
                    return True
                ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
                if ext in extensions:
                    return True
    except OSError:
        return False
    return False


def _nested_cover(directory: str, extensions: set[str]) -> str | None:
    try:
        with os.scandir(directory) as scan:
            children = [entry for entry in scan if entry.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    for entry in children:
        if _directory_is_book(entry.path, extensions):
            continue
        found = _named_cover(entry.path)
        if found:
            return found
    return None


def _find_source(root: str, folder: str, files, extensions: set[str], classify_root: str = "", classify_share: str = "") -> tuple[str, str] | None:
    folder_abs = placed_path(root, classify_root, classify_share, folder)
    named = _named_cover(folder_abs) or _nested_cover(folder_abs, extensions)
    if named:
        return named, "image"
    images = []
    primary = None
    for row in files:
        ext = (row["ext"] or "").lower()
        path = placed_path(root, classify_root, classify_share, row["relative_path"])
        name = row["relative_path"].rsplit("/", 1)[-1].lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            images.append((0 if name == "cover.jpg" else 1 if "cover" in name else 2, name, path))
        if row["is_primary"]:
            primary = (ext, path)
    images.sort()
    if images:
        return images[0][2], "image"
    if primary and primary[0] == ".epub":
        return primary[1], "epub"
    if primary and primary[0] == ".pdf":
        return primary[1], "pdf"
    return None


def _write_thumb(source: tuple[str, str], dest: Path) -> None:
    from PIL import Image

    path, kind = source
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        image = Image.open(path)
    elif kind == "epub":
        raw = _epub_cover_bytes(path)
        if not raw:
            raise FileNotFoundError("epub 没有封面")
        image = Image.open(io.BytesIO(raw))
    else:
        _write_pdf_thumb(path, dest)
        return
    image = image.convert("RGB")
    image.thumbnail((320, 480))
    image.save(dest, "JPEG", quality=80)


def _epub_cover_bytes(path: str) -> bytes | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = [
                name
                for name in zf.namelist()
                if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            ]
            names.sort(key=lambda name: (0 if "cover" in name.lower() else 1, name))
            if not names:
                return None
            return zf.read(names[0])
    except (OSError, zipfile.BadZipFile):
        return None


def _write_pdf_thumb(path: str, dest: Path) -> None:
    """PDF 封面放在子进程里生成。坏文件会让渲染库直接退出，不能带上网页进程。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [sys.executable, "-m", "app.covers", path, str(dest)],
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=25,
        capture_output=True,
    )
    if completed.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise RuntimeError("pdf 封面生成失败")


def _render_pdf_file(path: str, dest: str) -> None:
    import fitz
    from PIL import Image

    doc = fitz.open(path)
    try:
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(0.4, 0.4), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    image.thumbnail((320, 480))
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=80)


if __name__ == "__main__":
    _render_pdf_file(sys.argv[1], sys.argv[2])
