"""从 OPF、EPUB、PDF、MOBI 和文件名读取书目，并把标签写回 metadata.opf。"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
import zipfile
from xml.etree import ElementTree

ElementTree.register_namespace("", "http://www.idpf.org/2007/opf")
ElementTree.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ElementTree.register_namespace("opf", "http://www.idpf.org/2007/opf")

NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf",
}
_SERIES_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<series>.+?)\s+#(?P<index>\d+(?:\.\d+)?)\)\s*$")
_AUTHOR_RE = re.compile(r"^(?P<author>.+?)\s+-\s+(?P<title>.+)$")


def empty_meta(source: str, confidence: str) -> dict:
    return {
        "title": None,
        "subtitle": None,
        "authors": [],
        "tags": [],
        "series": None,
        "series_index": None,
        "language": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "description": None,
        "rating": None,
        "source": source,
        "confidence": confidence,
    }


_CALIBRE_ID_RE = re.compile(r"\s+\(\d{2,8}\)\s*$")
_PARENT_AUTHOR_RE = re.compile(r"^\[\([^)]*\)\]\s*(.+)$")
_ISBN_LABEL_RE = re.compile(r"ISBN(?:-1[03])?[^0-9]{0,12}([0-9Xx][0-9Xx\-\s]{8,22})", re.I)
_ISBN_978_RE = re.compile(r"97[89](?:[-\s]?\d){10}")


def parse_filename(name: str) -> dict:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name).strip()
    meta = empty_meta("filename", "low")
    author = None
    title = stem
    matched = _AUTHOR_RE.match(stem)
    if matched:
        author = matched.group("author").strip()
        title = matched.group("title").strip()
    series_match = _SERIES_RE.match(title)
    if series_match:
        title = series_match.group("title").strip()
        meta["series"] = series_match.group("series").strip()
        meta["series_index"] = series_match.group("index")
    else:
        title = _CALIBRE_ID_RE.sub("", title).strip()
    meta["title"] = title or stem or name
    meta["authors"] = [author] if author else []
    return meta


def parse_book_folder(name: str, parent_name: str = "") -> dict:
    """文件夹名作书名；父目录形如 [(国籍)] 作者 时，把它当作作者。"""
    meta = parse_filename(name)
    if meta["authors"] or not parent_name:
        return meta
    matched = _PARENT_AUTHOR_RE.match(parent_name.strip())
    if not matched:
        return meta
    author = re.sub(r"\s+", " ", matched.group(1)).strip()
    if author:
        meta["authors"] = [author]
    return meta


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _texts(parent, names: set[str]) -> list[str]:
    found = []
    for el in parent.iter():
        if _local(el.tag) in names:
            text = (el.text or "").strip()
            if text:
                found.append(text)
    return found


_PINYIN = frozenset(
    """
    a ai an ang ao ba bai ban bang bao bei ben beng bi bian biao bie bin bing bo bu
    ca cai can cang cao ce cen ceng cha chai chan chang chao che chen cheng chi chong chou chu
    chua chuai chuan chuang chui chun chuo ci cong cou cu cuan cui cun cuo
    da dai dan dang dao de dei den deng di dia dian diao die ding diu dong dou du duan dui dun duo
    e ei en eng er fa fan fang fei fen feng fo fou fu
    ga gai gan gang gao ge gei gen geng gong gou gu gua guai guan guang gui gun guo
    ha hai han hang hao he hei hen heng hong hou hu hua huai huan huang hui hun huo
    ji jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun
    ka kai kan kang kao ke kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo
    la lai lan lang lao le lei leng li lia lian liang liao lie lin ling liu long lou lu lv luan lue lun luo
    ma mai man mang mao me mei men meng mi mian miao mie min ming miu mo mou mu
    na nai nan nang nao ne nei nen neng ni nian niang niao nie nin ning niu nong nou nu nv nuan nue nuo
    o ou pa pai pan pang pao pei pen peng pi pian piao pie pin ping po pou pu
    qi qia qian qiang qiao qie qin qing qiong qiu qu quan que qun
    ran rang rao re ren reng ri rong rou ru rua ruan rui run ruo
    sa sai san sang sao se sen seng sha shai shan shang shao she shei shen sheng shi shou shu
    shua shuai shuan shuang shui shun shuo si song sou su suan sui sun suo
    ta tai tan tang tao te teng ti tian tiao tie ting tong tou tu tuan tui tun tuo
    wa wai wan wang wei wen weng wo wu
    xi xia xian xiang xiao xie xin xing xiong xiu xu xuan xue xun
    ya yan yang yao ye yi yin ying yo yong you yu yuan yue yun
    za zai zan zang zao ze zei zen zeng zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu
    zhua zhuai zhuan zhuang zhui zhun zhuo zi zong zou zu zuan zui zun zuo
    """.split()
)
_ENGLISH = frozenset(
    "the a an of and or to in on for with from by at as is it its this that how book books play edition volume vol series part novel story".split()
)
_WRAPPED_TITLE = re.compile(r"^《\s*(?P<title>[^》]+?)\s*》\s*(?:作者\s*[:：]\s*(?P<author>.+))?$")


def title_issue(title: str | None) -> str:
    """书名包着《》时返回 brackets，整段是拼音时返回 pinyin。"""
    text = (title or "").strip()
    if not text:
        return ""
    if "《" in text or "》" in text:
        return "brackets"
    if _is_pinyin_title(text):
        return "pinyin"
    return ""


def suggest_title(title: str | None) -> dict:
    """给出可一键改成的书名。authors 为 None 时不改作者。"""
    text = (title or "").strip()
    issue = title_issue(text)
    if issue == "brackets":
        matched = _WRAPPED_TITLE.match(text)
        if matched:
            author = matched.group("author")
            return {
                "title": matched.group("title").strip(),
                "authors": _people(author) if author else None,
                "issue": issue,
            }
        cleaned = re.sub(r"\s+", " ", text.replace("《", "").replace("》", "")).strip()
        return {"title": cleaned or text, "authors": None, "issue": issue}
    if issue == "pinyin":
        cleaned = re.sub(r"[_\s]+", " ", text).strip()
        return {"title": cleaned or text, "authors": None, "issue": issue}
    return {"title": text, "authors": None, "issue": ""}


def _is_pinyin_title(title: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", title):
        return False
    tokens = [item.lower() for item in re.findall(r"[A-Za-z]+", title) if len(item) > 1]
    if len(tokens) < 2:
        return False
    return all(token not in _ENGLISH and token in _PINYIN for token in tokens)


def _people(value: str) -> list[str] | None:
    text = re.sub(r"^[\[（(][^\]）)]+[\]）)]\s*", "", value.strip())
    names = [part.strip() for part in re.split(r"[、,，]", text) if part.strip()]
    return names or None


def clean_text(value: str | None) -> str | None:
    """丢掉 MOBI 全名里的二进制控制符，保留正常书名。"""
    if not value:
        return None
    controls = sum(1 for ch in value if ord(ch) < 32 and ch not in "\t\n")
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    if controls and len(text) < 2:
        return None
    if not text or not re.search(r"[0-9A-Za-z\u0080-\U0010ffff]", text):
        return None
    return text


def _isbn(values: list[str]) -> str | None:
    for value in values:
        digits = re.sub(r"[^0-9Xx]", "", value or "")
        if len(digits) in (10, 13):
            return digits.upper()
    return None


def _isbn_from_text(text: str) -> str | None:
    if not text:
        return None
    labeled = _isbn(_ISBN_LABEL_RE.findall(text))
    if labeled:
        return labeled
    return _isbn(_ISBN_978_RE.findall(text))


def _attr(el, name: str) -> str:
    for key, value in el.attrib.items():
        if _local(key).lower() == name:
            return value
    return ""


def parse_opf_bytes(data: bytes) -> dict | None:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None
    meta = empty_meta("opf", "high")
    titles = _texts(root, {"title"})
    if titles:
        meta["title"] = titles[0]
        if len(titles) > 1:
            meta["subtitle"] = titles[1]
    meta["authors"] = _texts(root, {"creator"})
    meta["tags"] = _texts(root, {"subject"})
    languages = _texts(root, {"language"})
    meta["language"] = languages[0] if languages else None
    publishers = _texts(root, {"publisher"})
    meta["publisher"] = publishers[0] if publishers else None
    dates = _texts(root, {"date"})
    meta["pubdate"] = dates[0] if dates else None
    descriptions = _texts(root, {"description"})
    if descriptions:
        meta["description"] = descriptions[0][:4000]
    isbn_values: list[str] = []
    other_ids: list[str] = []
    for el in root.iter():
        if _local(el.tag) != "identifier":
            continue
        scheme = _attr(el, "scheme").lower()
        text = (el.text or "").strip()
        if not text or scheme in {"uuid", "calibre", "mobi-asin", "asin", "uri"}:
            continue
        if "isbn" in scheme:
            isbn_values.append(text)
        else:
            other_ids.append(text)
    meta["isbn"] = _isbn(isbn_values) or _isbn(other_ids)
    for el in root.iter():
        if _local(el.tag) != "meta":
            continue
        key = (_attr(el, "name") or _attr(el, "property")).lower()
        content = (_attr(el, "content") or el.text or "").strip()
        if key in {"calibre:series", "belongs-to-collection"} and content:
            meta["series"] = content
        elif key in {"calibre:series_index", "group-position"} and content:
            meta["series_index"] = content
        elif key == "calibre:rating" and content:
            meta["rating"] = content
        elif key in {"calibre:isbn", "isbn"} and content and not meta["isbn"]:
            meta["isbn"] = _isbn([content])
        elif key in {"calibre:publisher", "publisher"} and content and not meta["publisher"]:
            meta["publisher"] = content
    if not any(meta.get(key) for key in ("title", "authors", "tags", "publisher", "isbn")):
        return None
    return meta


def merge_meta(primary: dict | None, extra: dict | None) -> dict | None:
    """OPF 优先。电子书内部只补上 OPF 里没有的书名、作者、出版社和 ISBN。"""
    if not primary:
        return extra
    if not extra:
        return primary
    for key in (
        "title",
        "subtitle",
        "publisher",
        "pubdate",
        "isbn",
        "description",
        "language",
        "series",
        "series_index",
        "rating",
    ):
        if not primary.get(key) and extra.get(key):
            primary[key] = extra[key]
    if not primary.get("authors") and extra.get("authors"):
        primary["authors"] = extra["authors"]
    if not primary.get("tags") and extra.get("tags"):
        primary["tags"] = extra["tags"]
    return primary


def write_opf_tags(path: str, tags: list[str], title: str | None = None, authors: list[str] | None = None) -> None:
    """把标签写入图书文件夹的 metadata.opf。没有该文件时新建，不改电子书正文。"""
    fields: dict = {"tags": tags}
    if not os.path.isfile(path):
        fields["title"] = title
        fields["authors"] = authors or []
    write_opf_fields(path, fields)


def write_opf_fields(path: str, fields: dict) -> None:
    """只更新 fields 里出现的书目字段。已有文件的其他内容保留，不改电子书正文。"""
    if os.path.isfile(path):
        try:
            tree = ElementTree.parse(path)
        except (ElementTree.ParseError, OSError) as exc:
            raise OSError(f"无法读取 metadata.opf：{exc}") from exc
        root = tree.getroot()
        meta = next((el for el in root.iter() if _local(el.tag) == "metadata"), None)
        if meta is None:
            meta = ElementTree.SubElement(root, "{http://www.idpf.org/2007/opf}metadata")
        _apply_opf_fields(meta, fields)
    else:
        tree = _new_opf(fields.get("title"), list(fields.get("authors") or []), list(fields.get("tags") or []))
        meta = next(el for el in tree.getroot().iter() if _local(el.tag) == "metadata")
        rest = {key: value for key, value in fields.items() if key not in {"title", "authors", "tags"}}
        if rest:
            _apply_opf_fields(meta, rest)
    _write_tree(path, tree)


def _apply_opf_fields(meta, fields: dict) -> None:
    if "title" in fields:
        _replace_dc(meta, "title", [fields.get("title") or ""])
    if "authors" in fields:
        _replace_dc(meta, "creator", list(fields.get("authors") or []))
    if "tags" in fields:
        _replace_dc(meta, "subject", list(fields.get("tags") or []))
    if "publisher" in fields:
        _replace_dc(meta, "publisher", [fields.get("publisher") or ""])
    if "isbn" in fields:
        _set_isbn(meta, (fields.get("isbn") or "").strip())
    if "description" in fields:
        _replace_dc(meta, "description", [fields.get("description") or ""])
    if "series" in fields:
        _set_meta_content(meta, ("calibre:series", "belongs-to-collection"), (fields.get("series") or "").strip())
    if "series_index" in fields:
        _set_meta_content(meta, ("calibre:series_index", "group-position"), str(fields.get("series_index") or "").strip())


def _replace_dc(meta, local: str, values: list[str]) -> None:
    dc = "{http://purl.org/dc/elements/1.1/}"
    for child in list(meta):
        if _local(child.tag) == local:
            meta.remove(child)
    seen: set[str] = set()
    for value in values:
        text = (value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ElementTree.SubElement(meta, dc + local).text = text


def _set_isbn(meta, isbn: str) -> None:
    found = False
    for child in list(meta):
        local = _local(child.tag)
        if local == "identifier" and "isbn" in _attr(child, "scheme").lower():
            found = True
            if isbn:
                child.text = isbn
            else:
                meta.remove(child)
        elif local == "meta" and (_attr(child, "name") or _attr(child, "property")).lower() in {"calibre:isbn", "isbn"}:
            found = True
            if isbn:
                _set_attr(child, "content", isbn)
            else:
                meta.remove(child)
    if isbn and not found:
        element = ElementTree.SubElement(meta, "{http://purl.org/dc/elements/1.1/}identifier")
        element.set("{http://www.idpf.org/2007/opf}scheme", "ISBN")
        element.text = isbn


def _set_meta_content(meta, names: tuple[str, ...], content: str) -> None:
    targets = {name.lower() for name in names}
    found = False
    for child in list(meta):
        if _local(child.tag) != "meta":
            continue
        key = (_attr(child, "name") or _attr(child, "property")).lower()
        if key not in targets:
            continue
        found = True
        if content:
            _set_attr(child, "content", content)
        else:
            meta.remove(child)
    if content and not found:
        element = ElementTree.SubElement(meta, "{http://www.idpf.org/2007/opf}meta")
        element.set("name", names[0])
        element.set("content", content)


def _set_attr(element, name: str, value: str) -> None:
    for key in list(element.attrib):
        if _local(key).lower() == name:
            element.attrib[key] = value
            return
    element.attrib[name] = value


def _write_tree(path: str, tree: ElementTree.ElementTree) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    temporary = path + ".bm-tmp"
    try:
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass


def _new_opf(title: str | None, authors: list[str], tags: list[str]) -> ElementTree.ElementTree:
    opf = "http://www.idpf.org/2007/opf"
    dc = "http://purl.org/dc/elements/1.1/"
    root = ElementTree.Element(f"{{{opf}}}package", {"version": "2.0", "unique-identifier": "uuid_id"})
    meta = ElementTree.SubElement(root, f"{{{opf}}}metadata")
    title_el = ElementTree.SubElement(meta, f"{{{dc}}}title")
    title_el.text = (title or "").strip() or "未命名"
    for author in authors:
        name = (author or "").strip()
        if not name:
            continue
        ElementTree.SubElement(meta, f"{{{dc}}}creator").text = name
    for tag in tags:
        ElementTree.SubElement(meta, f"{{{dc}}}subject").text = tag
    ident = ElementTree.SubElement(meta, f"{{{dc}}}identifier", {"id": "uuid_id"})
    ident.set(f"{{{opf}}}scheme", "uuid")
    ident.text = str(uuid.uuid4())
    return ElementTree.ElementTree(root)


def parse_opf_file(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            data = fh.read(2_000_000)
    except OSError:
        return None
    return parse_opf_bytes(data)


def parse_epub(path: str) -> dict | None:
    try:
        with zipfile.ZipFile(path) as zf:
            opf_name = _epub_opf_name(zf)
            if not opf_name:
                return None
            meta = parse_opf_bytes(zf.read(opf_name))
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    if not meta:
        return None
    meta["source"] = "epub"
    meta["confidence"] = "medium"
    return meta


def _epub_opf_name(zf: zipfile.ZipFile) -> str | None:
    try:
        container = zf.read("META-INF/container.xml")
    except KeyError:
        return None
    try:
        root = ElementTree.fromstring(container)
    except ElementTree.ParseError:
        return None
    for el in root.iter():
        if _local(el.tag) == "rootfile":
            full = el.attrib.get("full-path")
            if full:
                return full
    return None


def parse_pdf(path: str) -> dict | None:
    """只读文末的书目字典。不打开页面和 XMP，损坏的交叉引用表不会把整份 PDF 反复读上来。"""
    try:
        import logging

        from pypdf import PdfReader

        logging.getLogger("pypdf").setLevel(logging.ERROR)
        reader = PdfReader(path, strict=False, root_object_recovery_limit=0)
        info = reader.metadata
    except Exception:
        return None
    if not info:
        return None
    meta = empty_meta("pdf", "medium")
    meta["title"] = _pdf_text(getattr(info, "title", None))
    author = _pdf_text(getattr(info, "author", None))
    meta["authors"] = [part.strip() for part in re.split(r"[;,，、]", author) if part.strip()] if author else []
    subject = _pdf_text(getattr(info, "subject", None))
    keywords = _pdf_text(info.get("/Keywords") if hasattr(info, "get") else None)
    publisher = _pdf_text(info.get("/Publisher") if hasattr(info, "get") else None)
    meta["publisher"] = publisher
    meta["isbn"] = _isbn_from_text(" ".join(item for item in (subject, keywords, publisher) if item))
    if subject:
        meta["tags"] = [part.strip() for part in re.split(r"[;,，]", subject) if part.strip() and "isbn" not in part.lower()]
    if not any(meta.get(key) for key in ("title", "authors", "publisher", "isbn")):
        return None
    return meta


def _pdf_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_mobi_title(path: str) -> dict | None:
    try:
        with open(path, "rb") as fh:
            header = fh.read(78)
            if len(header) < 78:
                return None
            record = fh.read(8)
            if len(record) < 8:
                return None
            start = int.from_bytes(record[0:4], "big")
            fh.seek(start)
            chunk = fh.read(512 * 1024)
    except OSError:
        return None
    at = chunk.find(b"MOBI")
    if at < 0 or at + 8 > len(chunk):
        return None
    header_len = int.from_bytes(chunk[at + 4 : at + 8], "big")
    title = _mobi_full_name(chunk, at, start, path) if header_len >= 0x5C else None
    authors: list[str] = []
    publisher = None
    isbn = None
    exth_title = None
    trailing_title = None
    exth = at + header_len
    if 0 < header_len and exth + 12 <= len(chunk) and chunk[exth : exth + 4] == b"EXTH":
        exth_len = int.from_bytes(chunk[exth + 4 : exth + 8], "big")
        count = int.from_bytes(chunk[exth + 8 : exth + 12], "big")
        trailing_title = _text_at(chunk, exth + exth_len)
        pos = exth + 12
        for _ in range(min(count, 80)):
            if pos + 8 > len(chunk):
                break
            kind = int.from_bytes(chunk[pos : pos + 4], "big")
            length = int.from_bytes(chunk[pos + 4 : pos + 8], "big")
            if length < 8 or pos + length > len(chunk):
                break
            text = chunk[pos + 8 : pos + length].decode("utf-8", errors="ignore").strip("\x00 ").strip()
            if kind == 100 and text:
                authors.append(text)
            elif kind == 101 and text and not publisher:
                publisher = text
            elif kind == 104 and text and not isbn:
                isbn = _isbn([text])
            elif kind == 503 and text:
                exth_title = text
            pos += length
    title = clean_text(exth_title) or clean_text(title) or trailing_title
    authors = [item for item in (clean_text(author) for author in authors) if item]
    publisher = clean_text(publisher)
    if not any((title, authors, publisher, isbn)):
        return None
    meta = empty_meta("mobi", "medium" if authors or publisher or isbn else "low")
    meta["title"] = title
    meta["authors"] = authors
    meta["publisher"] = publisher
    meta["isbn"] = isbn
    return meta


def _text_at(chunk: bytes, pos: int) -> str | None:
    if pos < 0 or pos >= len(chunk):
        return None
    end = chunk.find(b"\x00", pos, pos + 500)
    if end <= pos:
        return None
    return clean_text(chunk[pos:end].decode("utf-8", errors="ignore"))


def _mobi_full_name(chunk: bytes, at: int, start: int, path: str) -> str | None:
    if at + 0x5C > len(chunk):
        return None
    name_off = int.from_bytes(chunk[at + 0x54 : at + 0x58], "big")
    name_len = int.from_bytes(chunk[at + 0x58 : at + 0x5C], "big")
    if name_len <= 0 or name_len > 2000:
        return None
    if start + name_off + name_len <= start + len(chunk) and name_off >= 0:
        raw = chunk[name_off : name_off + name_len]
    else:
        try:
            with open(path, "rb") as fh:
                fh.seek(start + name_off)
                raw = fh.read(name_len)
        except OSError:
            return None
    title = raw.decode("utf-8", errors="ignore").strip("\x00 ").strip()
    return title or None


def head_hash(path: str, nbytes: int = 65536) -> str | None:
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            chunk = fh.read(nbytes)
        if not chunk:
            return None
        digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def parse_primary(path: str, ext: str) -> dict | None:
    ext = ext.lower()
    if ext == ".epub":
        return parse_epub(path)
    if ext == ".pdf":
        return parse_pdf(path)
    if ext in {".mobi", ".azw", ".azw3", ".azw4"}:
        return parse_mobi_title(path)
    return None
