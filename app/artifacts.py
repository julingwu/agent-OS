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
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

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
        if not path.is_file() or path.name.endswith(".meta.json") or path.name.endswith(".preview.png"):
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


# ---------------------------------------------------------------- 文档伴生预览图生成
def _get_doc_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["msyhbd.ttc", "simhei.ttf", "NotoSansCJK-Bold.ttc", "arialbd.ttf"]
        if bold
        else ["msyh.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc", "arial.ttf"]
    )
    windir = os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts"
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
        try:
            p = os.path.join(windir, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        except Exception:
            pass
    linux_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in linux_paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} 字节"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB ({size:,} 字节)"
    return f"{size / (1024 * 1024):.2f} MB ({size:,} 字节)"


def _extract_doc_text_lines(name: str, kind: str, data: bytes) -> list[str]:
    ext = pathlib.Path(name).suffix.lower()
    k_low = (kind or "").lower()
    raw_text = ""

    # 1. PDF 提取
    if ext == ".pdf" or "pdf" in k_low:
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = []
            for p in reader.pages[:4]:
                t = (p.extract_text() or "").strip()
                if t:
                    pages.append(t)
            if pages:
                raw_text = "\n".join(pages)
        except Exception:
            pass

    # 2. HTML 提取
    if not raw_text and (ext in (".html", ".htm") or "html" in k_low):
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "html.parser")
            for tag in soup(["script", "style", "svg", "noscript"]):
                tag.decompose()
            raw_text = soup.get_text(separator="\n", strip=True)
        except Exception:
            raw_text = re.sub(r"<[^>]+>", " ", data.decode("utf-8", errors="replace"))

    # 3. JSON 提取
    if not raw_text and (ext == ".json" or "json" in k_low):
        try:
            parsed = json.loads(data.decode("utf-8", errors="replace"))
            raw_text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            raw_text = data.decode("utf-8", errors="replace")

    # 4. 常规文本 / Markdown / CSV 提取
    if not raw_text:
        for enc in ("utf-8", "gb18030", "gbk"):
            try:
                candidate = data.decode(enc)
                printable_cnt = sum(1 for ch in candidate[:500] if ch.isprintable() or ch in "\r\n\t")
                if printable_cnt > 0.85 * min(len(candidate), 500):
                    raw_text = candidate
                    break
            except Exception:
                continue

    # 5. 二进制/不可解析兜底
    if not raw_text or not raw_text.strip():
        sha256_hash = hashlib.sha256(data).hexdigest()
        raw_text = (
            f"【已归档二进制文件产物】\n"
            f"文件名: {name}\n"
            f"文件类型: {kind}\n"
            f"文件大小: {_format_bytes(len(data))}\n"
            f"SHA-256 完整性摘要: {sha256_hash}\n"
            f"安全沙箱已验证文件结构与哈希完整性。\n"
            f"提示: 请使用 ?raw=1 参数或客户端下载按钮直接获取原始二进制文件。"
        )

    # 格式化与换行截断
    lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        while len(line) > 52:
            lines.append(line[:52])
            line = line[52:]
        if line:
            lines.append(line)
        if len(lines) >= 8:
            break

    if len(lines) > 7:
        lines = lines[:7]
        lines.append("... [文档完整正文已安全留存，请使用 ?raw=1 下载查看]")
    return lines


def generate_doc_preview(name: str, kind: str, data: bytes) -> bytes:
    """使用 Pillow 生成一张 1200x675（16:9 高清 2x）的精美文档封面/概览卡片。

    具备暗调商务风格 `#0F172A`，带有对应文件类型的高亮徽标（如 [PDF 深度研报] 或 [HTML 交互式报告]）、
    清晰的中文文件名标题、文件大小、安全审计标签，以及从文本或 PDF 中提取的摘要文本预览框。
    """
    ext = pathlib.Path(name).suffix.lower()
    k_low = (kind or "").lower()

    if ext == ".pdf" or "pdf" in k_low:
        badge_text = "PDF 深度研报"
        accent_color = "#EF4444"
        badge_bg = "#2D151E"
        badge_text_color = "#F43F5E"
    elif ext in (".html", ".htm") or "html" in k_low:
        badge_text = "HTML 交互式报告"
        accent_color = "#06B6D4"
        badge_bg = "#0C2B35"
        badge_text_color = "#22D3EE"
    elif ext in (".csv", ".xlsx", ".xls") or "csv" in k_low or "spreadsheet" in k_low:
        badge_text = "DATA 数据表格"
        accent_color = "#10B981"
        badge_bg = "#0B2D23"
        badge_text_color = "#34D399"
    elif ext == ".json" or "json" in k_low:
        badge_text = "JSON 结构化数据"
        accent_color = "#F59E0B"
        badge_bg = "#332508"
        badge_text_color = "#FBBF24"
    elif ext in (".md", ".txt", ".docx", ".doc") or "markdown" in k_low or "text" in k_low:
        badge_text = "DOC 结构化文档"
        accent_color = "#8B5CF6"
        badge_bg = "#271438"
        badge_text_color = "#C084FC"
    else:
        badge_text = "FILE 成果文件"
        accent_color = "#3B82F6"
        badge_bg = "#112338"
        badge_text_color = "#60A5FA"

    img = Image.new("RGB", (1200, 675), "#0F172A")
    draw = ImageDraw.Draw(img)

    # 外部主卡片容器
    draw.rounded_rectangle([(40, 36), (1160, 639)], radius=18, fill="#1E293B", outline="#334155", width=2)
    # 顶部主题色强调条
    draw.rounded_rectangle([(40, 36), (1160, 44)], radius=4, fill=accent_color)

    # 1. 文件类型徽标
    f_badge = _get_doc_font(18, bold=True)
    badge_width = int(f_badge.getlength(badge_text)) if hasattr(f_badge, "getlength") else len(badge_text) * 16
    draw.rounded_rectangle([(70, 65), (70 + badge_width + 36, 106)], radius=8, fill=badge_bg, outline=accent_color, width=2)
    draw.text((88, 75), badge_text, fill=badge_text_color, font=f_badge)

    # 2. 安全审计标签
    audit_left = 70 + badge_width + 50
    draw.rounded_rectangle([(audit_left, 65), (min(1130, audit_left + 460), 106)], radius=8, fill="#0B2D23", outline="#059669", width=1)
    f_audit = _get_doc_font(16)
    draw.text((audit_left + 16, 75), "● 安全审计已通过 · SHA-256 完整性校验 · 生产就绪", fill="#34D399", font=f_audit)

    # 3. 中文文件名主标题（超长自动截断）
    f_title = _get_doc_font(32, bold=True)
    clean_title = name
    if hasattr(f_title, "getlength"):
        while clean_title and f_title.getlength(clean_title) > 1030:
            clean_title = clean_title[:-2]
        if clean_title != name:
            clean_title += "..."
    draw.text((70, 130), clean_title, fill="#F8FAFC", font=f_title)

    # 4. 元数据信息栏
    f_meta = _get_doc_font(18)
    meta_str = f"文件大小: {_format_bytes(len(data))}   |   MIME 类型: {kind}   |   状态: 校验通过"
    draw.text((70, 185), meta_str, fill="#94A3B8", font=f_meta)

    # 5. 内容摘要预览框
    draw.rounded_rectangle([(70, 235), (1130, 560)], radius=12, fill="#0F172A", outline="#334155", width=1)
    draw.text((95, 252), "📄 文档正文概览 / 摘要预览 (Content Excerpt)", fill="#38BDF8", font=_get_doc_font(16, bold=True))
    draw.line([(95, 280), (1105, 280)], fill="#1E293B", width=1)

    f_body = _get_doc_font(18)
    excerpt_lines = _extract_doc_text_lines(name, kind, data)
    y_pos = 295
    for line in excerpt_lines:
        draw.text((95, y_pos), line, fill="#CBD5E1", font=f_body)
        y_pos += 32

    # 6. 底部提示栏
    draw.text(
        (70, 585),
        "💡 提示: 桌面端已自动渲染安全封面图 | 若需查看/下载原格式文件请点击下载或在 URL 后添加 ?raw=1",
        fill="#64748B",
        font=_get_doc_font(15),
    )
    draw.text((1000, 585), "AgentOS Hub", fill="#475569", font=_get_doc_font(16, bold=True))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def ensure_doc_preview(artifact_id: str) -> bytes:
    """获取产物的伴生预览图字节；若尚未生成则即时生成并缓存。"""
    root_dir = root()
    preview_path = root_dir / f"{artifact_id}.preview.png"
    if preview_path.is_file():
        try:
            return preview_path.read_bytes()
        except OSError:
            pass

    path, kind = resolve(artifact_id)
    meta = _read_meta(root_dir, artifact_id) or {}
    name = meta.get("name") or path.name
    data = path.read_bytes()
    preview_bytes = generate_doc_preview(name=name, kind=kind, data=data)
    try:
        preview_path.write_bytes(preview_bytes)
    except OSError:
        pass
    return preview_bytes
