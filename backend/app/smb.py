"""用 Windows 的 net use 连接 Linux NAS 上的 Samba 共享。"""

from __future__ import annotations

import os
import subprocess

from .paths import split_rel, writable_dir


def parse_smb_location(text: str) -> tuple[str, str, str]:
    """把 smb://地址/共享/子路径 拆成地址、共享、子路径。只写地址时共享和子路径为空。"""
    raw = (text or "").strip()
    lowered = raw.lower()
    for prefix in ("smb://", "unc://"):
        if lowered.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    host = parts[0] if parts else ""
    share = parts[1] if len(parts) > 1 else ""
    subpath = "/".join(parts[2:]) if len(parts) > 2 else ""
    return host, share, subpath


def normalize_host(host: str) -> str:
    return parse_smb_location(host)[0]


def split_share_path(text: str) -> tuple[str, str]:
    """把「书库」或「书库/子目录」拆成共享名和子路径。"""
    parts = [part for part in (text or "").strip().strip("/\\").replace("\\", "/").split("/") if part]
    if not parts:
        return "", ""
    return parts[0], "/".join(parts[1:])


def is_filesystem_path(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.startswith("\\\\") or raw.startswith("/") or raw.startswith("\\"):
        return True
    return len(raw) > 2 and raw[1] == ":"


def build_unc(host: str, share: str, subpath: str = "") -> str:
    host = normalize_host(host)
    share = (share or "").strip().strip("\\/")
    if not host or any(ch in host for ch in '<>:"|?*'):
        raise ValueError("SMB 地址无效")
    if not share or any(ch in share for ch in '<>:"|?*\\/'):
        raise ValueError("共享名无效")
    parts = split_rel(subpath)
    root = f"\\\\{host}\\{share}"
    if parts:
        root = root + "\\" + "\\".join(parts)
    return root


def share_unc(host: str, share: str) -> str:
    return build_unc(host, share, "")


def connect_share(host: str, share: str, username: str, password: str, force: bool = False) -> None:
    if os.name != "nt":
        raise ValueError("SMB 连接目前只支持在 Windows 上运行")
    username = (username or "").strip()
    if not username:
        raise ValueError("请填写 SMB 账户")
    if not password:
        raise ValueError("请填写 SMB 密码")
    target = share_unc(host, share)
    if not force and os.path.isdir(target):
        return
    if force:
        subprocess.run(["net", "use", target, "/delete", "/y"], capture_output=True)
    result = subprocess.run(
        ["net", "use", target, password, f"/user:{username}", "/persistent:no"],
        capture_output=True,
    )
    if result.returncode == 0:
        return
    output = _decode(result.stdout) + _decode(result.stderr)
    if os.path.isdir(target) and _already_connected(output):
        return
    raise ValueError(_friendly(output))


def connect_and_prepare(host: str, share: str, subpath: str, username: str, password: str) -> str:
    connect_share(host, share, username, password, force=True)
    root = build_unc(host, share, subpath)
    if not os.path.isdir(root):
        raise ValueError("已连上共享，但书库目录不存在")
    if not writable_dir(root):
        raise ValueError("书库目录不可写，请确认账户有修改权限")
    return root


def _decode(data: bytes) -> str:
    encoding = "mbcs" if os.name == "nt" else "utf-8"
    return data.decode(encoding, errors="replace")


def _already_connected(text: str) -> bool:
    lowered = text.lower()
    return "1219" in text or "85" in text or "already" in lowered or "已经" in text or "已在使用" in text


def _friendly(text: str) -> str:
    compact = " ".join(text.split())
    if "1219" in compact or "多重连接" in compact:
        return "这台 NAS 已用别的账户连接。请先在资源管理器断开该共享，再保存。"
    if any(token in compact for token in ("1326", "系统错误 86", "System error 86", "用户名或密码", "登录失败", "Logon failure")):
        return "SMB 账户或密码不正确"
    if any(
        token in compact
        for token in (
            "系统错误 53",
            "系统错误 67",
            "系统错误 64",
            "系统错误 1231",
            "System error 53",
            "System error 64",
            "System error 67",
            "System error 1231",
            "找不到网络",
            "no longer available",
            "network path was not found",
        )
    ):
        return "找不到该 SMB 地址或共享名"
    if "系统错误 5" in compact or "System error 5" in compact:
        return "SMB 账户没有访问这个共享的权限"
    return compact or "无法连接 SMB"
