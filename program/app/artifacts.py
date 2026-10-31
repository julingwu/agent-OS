"""Artifact 存储：把二进制产物（截图、导出文件）落盘，事件里只留引用。

为什么不直接把图片塞进事件：
    ``app/events.py`` 的 ``MAX_PAYLOAD_BYTES = 8KB``。一张 1600px 宽的截图 base64
    就是几百 KB 到几 MB，塞进 ``artifact.attached`` 会同时炸掉事件表、WSS 帧和
    模型上下文。所以约定是：**字节落盘，事件只放 {artifact_id, name, kind, bytes, url}**。

目录约定：
    ``<workspace>/artifacts/<id><ext>``          ← 真正的字节
    ``<workspace>/artifacts/<id>.meta.json``     ← 展示用元数据（原名/类型/大小/时间）
    放在工作区根下，和「资料库」是同一个 jail，用户在界面里也能翻到、能删。

安全约束：
    * id 只能由本模块生成（``art_`` + 16 位 hex），下载接口拿它当白名单，
      用户可控的文件名永远不参与拼路径，从根上断掉 ``../`` 穿越；
    * 大小有上限（``MAX_BYTES``），避免一个失控的 exe 把磁盘写满；
    * 文件名只用于展示：去掉路径分隔符、控制字符、超长截断。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import pathlib
import re
import secrets
from datetime import datetime

# 8MB 与 exe 端的上限一致（desktop/src-tauri/src/main.rs MAX_ARTIFACT_BYTES）
MAX_BYTES = 8 * 1024 * 1024
MAX_NAME_CHARS = 120
DEFAULT_LIST_LIMIT = 50

ID_RE = re.compile(r"^art_[0-9a-f]{16}$")

# 已知类型 → 落盘扩展名（未知类型退回原名后缀 / .bin）
KIND_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/json": ".json",
    "application/pdf": ".pdf",
    "text/csv": ".csv",
}

# 没有 meta 时的兜底识别（只看文件头，够用且不需要额外依赖）
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)


# ---------------------------------------------------------------- 路径
def _workspace() -> pathlib.Path:
    """工作区根：优先用 agent.tools.WORKSPACE（资料库锁的就是这个根），退回 config。"""
    try:
        from .agent import tools as _tools

        return pathlib.Path(_tools.WORKSPACE)
    except Exception:                       # noqa: BLE001 - 导入期循环/未初始化都不该炸
        try:
            from .config import get_workspace_root

            return get_workspace_root()
        except Exception:                   # noqa: BLE001
            return pathlib.Path(os.environ.get("M0_WORKSPACE") or os.getcwd())


def root() -> pathlib.Path:
    p = _workspace() / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_name(name: str | None, fallback: str = "artifact") -> str:
    raw = os.path.basename((name or "").replace("\\", "/")).strip()
    raw = "".join(ch for ch in raw if ch.isprintable() and ch not in '<>:"|?*')
    raw = raw.strip("./ ")
    if not raw:
        raw = fallback
    if len(raw) > MAX_NAME_CHARS:
        stem, dot, ext = raw.rpartition(".")
        if dot and len(ext) <= 8:
            raw = stem[: MAX_NAME_CHARS - len(ext) - 1] + "." + ext
        else:
            raw = raw[:MAX_NAME_CHARS]
    return raw


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


# ---------------------------------------------------------------- 写入
def save_bytes(name: str, data: bytes, kind: str = "image/png") -> dict:
    """落盘一段字节，返回 ``{id, name, kind, bytes, path, url, created_at}``。

    调用方拿到 info 后**只能**把 id/name/kind/bytes/url 放进事件 payload，
    路径可以放（本机排查方便），但绝不能放 base64 正文。
    """
    if not data:
        raise ValueError("artifact 内容为空")
    if len(data) > MAX_BYTES:
        raise ValueError(f"artifact 太大：{len(data)} 字节 > 上限 {MAX_BYTES}")

    kind = (kind or "application/octet-stream").split(";")[0].strip().lower()
    clean = _safe_name(name)
    ext = KIND_EXT.get(kind) or pathlib.Path(clean).suffix.lower() or ".bin"
    art_id = "art_" + secrets.token_hex(8)

    root_dir = root()
    path = root_dir / f"{art_id}{ext}"
    path.write_bytes(data)

    info = {
        "id": art_id,
        "name": clean,
        "kind": kind,
        "bytes": len(data),
        "file": path.name,
        "path": str(path),
        "url": f"/v1/artifacts/{art_id}",
        "created_at": _now(),
    }
    # meta 写失败不算致命：下载接口可以靠文件头嗅探类型，只是丢了原标题
    try:
        (root_dir / f"{art_id}.meta.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError:
        pass
    return info


def save_b64(name: str, kind: str, data_b64: str) -> dict:
    """从 exe 的 WS 消息里收图：base64 解码后落盘。

    容忍 ``data:image/png;base64,`` 前缀（有些客户端会带），解码失败抛 ValueError，
    由调用方决定是回错误还是忽略——这里不吞异常，否则会出现"文件在但打不开"。
    """
    raw = (data_b64 or "").strip()
    if not raw:
        raise ValueError("artifact data_b64 为空")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    # 小于这个长度必然不是合法 base64，先挡掉避免一次无意义的异常开销
    if len(raw) < 8:
        raise ValueError("artifact data_b64 太短")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"artifact base64 解码失败：{exc}") from exc
    return save_bytes(name, data, kind)


# ---------------------------------------------------------------- 读取
def _sniff_kind(path: pathlib.Path) -> str:
    try:
        head = path.open("rb").read(16)
    except OSError:
        return "application/octet-stream"
    for magic, kind in _MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _read_meta(root_dir: pathlib.Path, art_id: str) -> dict | None:
    meta_path = root_dir / f"{art_id}.meta.json"
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve(artifact_id: str) -> tuple[pathlib.Path, str]:
    """id → (文件路径, MIME)。找不到抛 FileNotFoundError。"""
    art_id = str(artifact_id or "")
    if not ID_RE.match(art_id):          # 只认本模块生成的 id：路径穿越在这里就被挡住
        raise FileNotFoundError(art_id)
    root_dir = root()
    meta = _read_meta(root_dir, art_id) or {}

    candidates: list[pathlib.Path] = []
    file_name = pathlib.Path(str(meta.get("file") or "")).name
    if file_name:
        candidates.append(root_dir / file_name)
    candidates.extend(sorted(root_dir.glob(f"{art_id}.*")))

    for path in candidates:
        if not path.is_file() or path.name.endswith(".meta.json"):
            continue
        if path.parent.resolve() != root_dir.resolve():
            continue
        kind = str(meta.get("kind") or "") or _sniff_kind(path)
        return path, kind
    raise FileNotFoundError(art_id)


def describe(artifact_id: str) -> dict:
    """id → 元数据（没有 meta 时用文件本身凑一个）。"""
    path, kind = resolve(artifact_id)
    meta = _read_meta(root(), artifact_id) or {}
    return {
        "id": artifact_id,
        "name": meta.get("name") or path.name,
        "kind": kind,
        "bytes": path.stat().st_size,
        "url": f"/v1/artifacts/{artifact_id}",
        "created_at": meta.get("created_at") or "",
    }


def list_artifacts(limit: int = DEFAULT_LIST_LIMIT) -> list[dict]:
    """最近的 artifact（按落盘时间倒序）。给 /v1/artifacts 和排查用。"""
    root_dir = root()
    metas = []
    for meta_path in root_dir.glob("art_*.meta.json"):
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    metas.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    items = []
    for meta in metas[: max(1, min(int(limit), 200))]:
        path = root_dir / pathlib.Path(str(meta.get("file") or "")).name
        items.append({
            "id": meta.get("id", ""),
            "name": meta.get("name", ""),
            "kind": meta.get("kind", ""),
            "bytes": meta.get("bytes", 0),
            "url": meta.get("url") or f"/v1/artifacts/{meta.get('id', '')}",
            "created_at": meta.get("created_at", ""),
            "exists": path.is_file(),
        })
    return items
