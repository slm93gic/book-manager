"""用《中国图书馆分类法》给未分类图书推荐类目。

标准表来自公开的中图法类目数据，缓存在本机 data/clc.json。
只拿这本书自己的书名和标签去对类名，不读已整理书库里的标签。
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

CLC_URL = "https://raw.githubusercontent.com/acdzh/Chinese-Library-Classification/master/clc.json"
_GENERIC = {
    "中国",
    "世界",
    "理论",
    "总论",
    "概论",
    "概述",
    "通论",
    "方法",
    "研究",
    "应用",
    "其他",
    "各国",
    "基础",
    "综合",
    "一般性问题",
}
_DELIMITERS = "、，,；;"
_INDEXES: dict[tuple[str, int, int], "ClcIndex"] = {}


def standard_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "clc.json"


def ensure_standard(path: Path | None = None) -> Path:
    path = path or standard_path()
    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(CLC_URL, headers={"User-Agent": "book-manager"})
    with urllib.request.urlopen(request, timeout=90) as response, path.open("wb") as out:
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    return path


def classification_stamp(path: Path | None = None) -> str:
    source = ensure_standard(path)
    stat = source.stat()
    return f"clc5:{stat.st_size}:{stat.st_mtime_ns}"


class _Node:
    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.item: tuple[str, str] | None = None
        self.extension: tuple[str, str] | None = None


class ClcIndex:
    def __init__(self, classes: list[tuple[str, str]]) -> None:
        self.root = _Node()
        self.by_part: dict[str, tuple[str, str]] = {}
        for name, code in classes:
            self._put_name(name, code)
            for part in _parts(name):
                self._put_part(part, name, code)

    def suggest(self, title: str | None, tags_text: str | None) -> str:
        matches: list[tuple[int, int, int, int, str, str]] = []
        if title and title.strip():
            self._collect(title.strip(), exact_text=title.strip(), matches=matches, exact_bonus=0)
        for tag in _name_list(tags_text):
            self._collect(tag, exact_text=tag, matches=matches, exact_bonus=1)
            if tag in self.by_part:
                name, code = self.by_part[tag]
                if name not in _GENERIC:
                    matches.append(_rank(name, code, exact=1))
            if len(tag) >= 2:
                extended = self._extension(tag)
                if extended and extended[0] not in _GENERIC:
                    matches.append(_rank(extended[0], extended[1], exact=1))
        if not matches:
            return ""
        _exact, _length, _curriculum, _code_len, code, name = max(matches)
        return f"{code} {name}"

    def _put_name(self, name: str, code: str) -> None:
        if len(name) < 2 or name in _GENERIC:
            return
        node = self.root
        for char in name:
            node = node.children.setdefault(char, _Node())
            if node.extension is None or _shorter_name(name, code, node.extension):
                node.extension = (name, code)
        if node.item is None or _prefer(code, node.item[1]):
            node.item = (name, code)

    def _put_part(self, part: str, name: str, code: str) -> None:
        if len(part) < 2 or part in _GENERIC or part == name:
            return
        current = self.by_part.get(part)
        if current is None or _prefer(code, current[1]):
            self.by_part[part] = (name, code)

    def _extension(self, text: str) -> tuple[str, str] | None:
        node = self.root
        for char in text:
            child = node.children.get(char)
            if child is None:
                return None
            node = child
        if node.item is not None:
            return None
        return node.extension

    def _collect(self, text: str, exact_text: str, matches: list, exact_bonus: int) -> None:
        for name, code in _find(self.root, text):
            if name in _GENERIC:
                continue
            exact = exact_bonus if name == exact_text else 0
            matches.append(_rank(name, code, exact))


def load_index(path: Path | None = None) -> ClcIndex:
    source = ensure_standard(path)
    stat = source.stat()
    key = (str(source), stat.st_size, stat.st_mtime_ns)
    cached = _INDEXES.get(key)
    if cached is not None:
        return cached
    classes = _flatten(json.loads(source.read_text(encoding="utf-8")))
    index = ClcIndex(classes)
    _INDEXES.clear()
    _INDEXES[key] = index
    return index


def suggest_clc(title: str | None, tags_text: str | None, path: Path | None = None) -> str:
    return load_index(path).suggest(title, tags_text)


def _flatten(nodes: list) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(items: list) -> None:
        for node in items:
            code = str(node.get("id") or "").strip().strip("[]{}")
            name = str(node.get("desc") or "").strip()
            if code and name:
                found.append((name, code))
            children = node.get("children") or []
            if children:
                walk(children)

    walk(nodes)
    return found


def _parts(name: str) -> list[str]:
    return [part.strip() for part in re.split(f"[{re.escape(_DELIMITERS)}]", name) if part.strip()]


def _name_list(text: str | None) -> list[str]:
    return [item.strip() for item in re.split(r"[、，,；;]", text or "") if item.strip()]


def _curriculum(code: str) -> bool:
    return code.startswith("G62") or code.startswith("G63")


def _shorter_name(name: str, code: str, current: tuple[str, str]) -> bool:
    old_name, old_code = current
    return (len(name), _curriculum(code), len(code), code) < (len(old_name), _curriculum(old_code), len(old_code), old_code)


def _prefer(code: str, previous: str) -> bool:
    """同名类目保留更总的一条，教材用的复分靠后。"""
    return (_curriculum(code), len(code), code) < (_curriculum(previous), len(previous), previous)


def _rank(name: str, code: str, exact: int) -> tuple[int, int, int, int, str, str]:
    curriculum = 0 if _curriculum(code) else 1
    return (exact, len(name), curriculum, -len(code), code, name)


def _find(root: _Node, text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for start in range(len(text)):
        node = root
        for char in text[start:]:
            child = node.children.get(char)
            if child is None:
                break
            node = child
            if node.item is not None:
                found.append(node.item)
    return found
