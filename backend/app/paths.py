"""书库路径、书夹判定与 Windows 长路径。"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
from dataclasses import dataclass

DEFAULT_EXCLUDES = [
    "@eaDir",
    "#recycle",
    "@Recycle",
    ".Trash",
    ".Trash-*",
    "lost+found",
    "#snapshot",
]
DEFAULT_EXTENSIONS = [
    "epub",
    "pdf",
    "mobi",
    "azw",
    "azw3",
    "azw4",
    "kfx",
    "djvu",
    "cbz",
    "cbr",
    "fb2",
]
FORMAT_RANK = {
    ".epub": 0,
    ".pdf": 1,
    ".mobi": 2,
    ".azw3": 3,
    ".azw": 4,
    ".azw4": 5,
    ".kfx": 6,
    ".fb2": 7,
    ".djvu": 8,
    ".cbz": 9,
    ".cbr": 10,
}
_ILLEGAL = set('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


@dataclass
class DirEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    is_link: bool


def to_long(path: str) -> str:
    path = os.path.abspath(path)
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def from_long(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def display_path(path: str) -> str:
    return from_long(path)


def split_rel(relative: str) -> list[str]:
    relative = (relative or "").replace("\\", "/").strip()
    if not relative:
        return []
    parts = []
    for part in relative.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("路径越出书库")
        parts.append(part)
    return parts


def _inside_root(root: str, full: str) -> bool:
    """判断 full 是否仍在 root 内。UNC 共享根本身会被 Windows 当成盘符，不能用 commonpath。"""
    root_n = os.path.normcase(os.path.normpath(root))
    full_n = os.path.normcase(os.path.normpath(full))
    if full_n == root_n:
        return True
    prefix = root_n.rstrip("\\/") + os.sep
    return full_n.startswith(prefix)


def confined(root: str, relative: str) -> str:
    """把书库内相对路径解析为绝对路径，拒绝越出书库根。"""
    parts = split_rel(relative)
    root_abs = os.path.abspath(root)
    full = root_abs if not parts else os.path.abspath(os.path.join(root_abs, *parts))
    if not _inside_root(root_abs, full):
        raise ValueError("路径越出书库")
    return to_long(full)


def rel_posix(root: str, path: str) -> str:
    rel = os.path.relpath(from_long(path), os.path.abspath(root))
    if rel == ".":
        return ""
    rel = rel.replace("\\", "/")
    if rel.startswith("../") or rel == "..":
        raise ValueError("路径越出书库")
    return rel


def parent_rel(relative: str) -> str | None:
    relative = (relative or "").replace("\\", "/").strip("/")
    if not relative:
        return None
    if "/" not in relative:
        return ""
    return relative.rsplit("/", 1)[0]


def placed_path(library_root: str, classify_root: str, classify_share: str, relative: str) -> str:
    """原始目录相对书库根，分类目录相对 books 这类共享。未配置分类共享时全部落在书库根。"""
    relative = (relative or "").replace("\\", "/").strip("/")
    share = (classify_share or "").replace("\\", "/").strip("/")
    if classify_root and share and (relative == share or relative.startswith(share + "/")):
        inner = "" if relative == share else relative[len(share) + 1 :]
        return confined(classify_root, inner)
    return confined(library_root, relative)


def leaf_name(relative: str) -> str:
    relative = (relative or "").replace("\\", "/").strip("/")
    if not relative:
        return ""
    return relative.rsplit("/", 1)[-1]


def join_rel(parent: str, name: str) -> str:
    parent = (parent or "").strip("/")
    if not parent:
        return name
    return f"{parent}/{name}"


def replace_prefix(path: str, old: str, new: str) -> str | None:
    if path == old:
        return new
    prefix = old + "/"
    if path.startswith(prefix):
        return new + path[len(old) :]
    return None


def name_error(name: str) -> str | None:
    if name is None:
        return "名称无效"
    if name != name.strip() or not name.strip():
        return "名称无效"
    if name in (".", ".."):
        return "名称无效"
    if name.endswith(" ") or name.endswith("."):
        return "名称不能以空格或点结尾"
    if any(ch in _ILLEGAL or ord(ch) < 32 for ch in name):
        return "名称含有 Windows 无法表示的字符"
    stem = name.split(".", 1)[0].upper()
    if stem in _RESERVED:
        return "名称是 Windows 保留名"
    return None


def name_taken(parent_dir: str, name: str) -> bool:
    """按目录列举的原始大小写比较，不把 Book 与 book 当成同一个名字。"""
    try:
        with os.scandir(to_long(parent_dir)) as it:
            for entry in it:
                if entry.name == name:
                    return True
    except OSError:
        return False
    return False


def suffixed_name(parent_dir: str, name: str) -> str:
    if not name_taken(parent_dir, name):
        return name
    for n in range(2, 1000):
        candidate = f"{name} ({n})"
        if not name_taken(parent_dir, candidate):
            return candidate
    raise RuntimeError("无法生成不冲突的名称")


def excluded_name(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def glob_match(relative: str, pattern: str) -> bool:
    if not pattern:
        return False
    regex = re.escape(pattern.strip()).replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", ".")
    text = relative.replace("\\", "/")
    if re.fullmatch(regex, text):
        return True
    return re.fullmatch(regex, text.rsplit("/", 1)[-1]) is not None


def included_book(relative: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    return any(glob_match(relative, pattern) for pattern in patterns)


def link_escapes(root: str, path: str) -> bool:
    plain = from_long(path)
    if not os.path.islink(plain):
        return False
    try:
        target = os.path.realpath(plain)
        root_real = os.path.realpath(os.path.abspath(root))
    except OSError:
        return True
    left = os.path.normcase(root_real) if os.name == "nt" else root_real
    right = os.path.normcase(target) if os.name == "nt" else target
    try:
        common = os.path.commonpath([left, right])
    except ValueError:
        return True
    return common != left


def unc_host(path: str) -> str:
    """UNC 主机名。本地路径返回空字符串。"""
    plain = from_long(path).replace("/", "\\")
    if not plain.startswith("\\\\"):
        return ""
    return plain[2:].split("\\", 1)[0].casefold()


def same_unc_server(left: str, right: str) -> bool:
    host = unc_host(left)
    return bool(host) and host == unc_host(right)


def _file_size(path: str) -> int:
    if os.path.islink(path):
        return 0
    return os.path.getsize(path)


def _rmtree(path: str) -> None:
    def retry(func, item, _exc):
        os.chmod(item, stat.S_IWRITE)
        func(item)

    shutil.rmtree(path, onexc=retry)


def _server_side_move(src: str, dst: str) -> None:
    """书库和 books 在同一块磁盘上，但是两个共享。Windows 不允许直接改名。

    CopyFile2 让 NAS 自己复制，文件内容不经过这台电脑。复制时核对每个文件的大小。
    """
    if os.path.exists(dst):
        raise FileExistsError(dst)
    os.makedirs(dst)
    try:
        for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
            rel = os.path.relpath(dirpath, src)
            target = dst if rel == "." else os.path.join(dst, rel)
            for name in dirnames:
                os.makedirs(os.path.join(target, name), exist_ok=True)
            for name in filenames:
                source = os.path.join(dirpath, name)
                copied = os.path.join(target, name)
                before = _file_size(source)
                shutil.copy2(source, copied, follow_symlinks=False)
                if _file_size(copied) != before:
                    raise OSError("复制后文件大小不一致")
    except Exception:
        if os.path.exists(dst):
            _rmtree(dst)
        raise
    try:
        _rmtree(src)
    except OSError as exc:
        raise OSError("文件已复制到分类目录，但源目录未能删除") from exc


def relocate_directory(src: str, dst: str) -> None:
    """同一共享内直接改名。同一台 NAS 的两个共享只在服务器上复制后再删除源目录。"""
    try:
        os.rename(src, dst)
        return
    except OSError as exc:
        if io_kind(exc) != "跨文件系统" or not same_unc_server(src, dst):
            raise
    _server_side_move(src, dst)


def io_kind(exc: OSError) -> str:
    win = getattr(exc, "winerror", None)
    text = str(exc)
    lowered = text.lower()
    if win == 17 or getattr(exc, "errno", None) == 18 or "different disk" in lowered or "不同的磁盘" in text:
        return "跨文件系统"
    if win in {53, 59, 64, 67, 121, 1231}:
        return "断线"
    if win in {32, 33}:
        return "占用"
    if win in {5, 1326}:
        return "权限"
    if isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {1, 13}:
        return "权限"
    if isinstance(exc, FileExistsError) or win == 183 or "already exists" in lowered or "当文件已存在" in text:
        return "同名"
    return "失败"


def list_children(path: str) -> list[DirEntry]:
    entries: list[DirEntry] = []
    with os.scandir(to_long(path)) as it:
        for entry in it:
            is_link = entry.is_symlink()
            try:
                is_dir = entry.is_dir(follow_symlinks=is_link)
            except OSError:
                continue
            size = 0
            mtime = 0.0
            if not is_dir:
                try:
                    stat = entry.stat(follow_symlinks=False)
                    size = int(stat.st_size)
                    mtime = float(stat.st_mtime)
                except OSError:
                    pass
            else:
                try:
                    mtime = float(entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    mtime = 0.0
            entries.append(
                DirEntry(
                    name=entry.name,
                    path=to_long(os.path.join(from_long(path), entry.name)),
                    is_dir=is_dir,
                    size=size,
                    mtime=mtime,
                    is_link=is_link,
                )
            )
    return entries


def writable_dir(path: str) -> bool:
    probe = os.path.join(from_long(path), ".__bm_write_test")
    try:
        with open(to_long(probe), "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(to_long(probe))
        return True
    except OSError:
        return False
