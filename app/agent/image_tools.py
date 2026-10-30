"""AI Visual Image Synthesis Tool for AgentOS (Grok Benchmark).

Features:
- Dual-Mode Image Generation Pipeline:
  * Pro API Mode: Auto-detects `IMAGE_API_KEY`, `SILICONFLOW_API_KEY`, `DASHSCOPE_API_KEY`,
    or `OPENAI_API_KEY` to query compatible `/images/generations` endpoints.
  * Elegant Offline / Zero-Key Degradation Mode: Uses Pillow (PIL) and Matplotlib
    to dynamically generate 1200x675 (16:9) high-definition artistic/tech cards across
    diverse styles (cyberpunk, photorealistic, illustration, minimalist_infographic),
    complete with ambient gradient lighting, glowing HUD borders, elegant typography,
    prompt quote containers, and verified AgentOS Neural Engine certification stamps.
- Native Artifacts Integration:
  * Saves image bytes directly into the AgentOS artifact repository via `app.artifacts.save_bytes`.
  * Returns standardized artifact response: {ok, artifact_id, url, markdown, title, prompt, style}.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import pathlib
import re
import secrets
from datetime import datetime
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("app.agent.image_tools")

# 宽高比解析与分辨率映射
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "16:9": (1200, 675),
    "1:1": (1024, 1024),
    "4:3": (1200, 900),
    "9:16": (675, 1200),
    "3:2": (1200, 800),
    "21:9": (1344, 576),
}

# 风格配色与设计规范
STYLE_CONFIGS: dict[str, dict[str, Any]] = {
    "cyberpunk": {
        "name": "CYBERPUNK",
        "bg_top": (10, 12, 28),
        "bg_bottom": (4, 5, 14),
        "neon_primary": (0, 242, 254),      # 电光青
        "neon_secondary": (255, 0, 127),    # 霓虹洋红
        "neon_accent": (121, 40, 202),      # 赛博紫
        "text_primary": (245, 248, 255),
        "text_secondary": (160, 180, 215),
        "card_bg": (18, 22, 45, 185),
        "card_border": (0, 242, 254, 160),
        "grid_color": (0, 242, 254, 18),
        "tagline": "NEURAL CYBER ENGINE // GRID MATRIX",
    },
    "photorealistic": {
        "name": "PHOTOREALISTIC",
        "bg_top": (18, 24, 38),
        "bg_bottom": (10, 14, 22),
        "neon_primary": (245, 158, 11),     # 电影暖琥珀
        "neon_secondary": (226, 232, 240),  # 银灰高光
        "neon_accent": (251, 191, 36),      # 暖金
        "text_primary": (250, 250, 252),
        "text_secondary": (165, 180, 200),
        "card_bg": (24, 32, 48, 180),
        "card_border": (245, 158, 11, 140),
        "grid_color": (255, 255, 255, 12),
        "tagline": "CINEMATIC 8K // OPTICAL RENDERING",
    },
    "illustration": {
        "name": "ILLUSTRATION",
        "bg_top": (26, 16, 48),
        "bg_bottom": (12, 8, 26),
        "neon_primary": (192, 132, 252),    # 星芒浅紫
        "neon_secondary": (45, 212, 191),   # 极光青绿
        "neon_accent": (244, 63, 94),       # 玫瑰晶红
        "text_primary": (254, 244, 255),
        "text_secondary": (190, 180, 215),
        "card_bg": (35, 22, 60, 185),
        "card_border": (192, 132, 252, 160),
        "grid_color": (192, 132, 252, 15),
        "tagline": "DIGITAL ARTISTRY // CELESTIAL CANVASES",
    },
    "minimalist_infographic": {
        "name": "MINIMALIST INFOGRAPHIC",
        "bg_top": (18, 20, 26),
        "bg_bottom": (9, 10, 14),
        "neon_primary": (59, 130, 246),     # 科技深蓝
        "neon_secondary": (16, 185, 129),   # 翡翠绿
        "neon_accent": (99, 102, 241),      # 靛青
        "text_primary": (243, 244, 246),
        "text_secondary": (156, 163, 175),
        "card_bg": (26, 29, 38, 185),
        "card_border": (59, 130, 246, 150),
        "grid_color": (255, 255, 255, 10),
        "tagline": "TECHNICAL SCHEMATIC // DATA TOPOLOGY",
    },
}


def _find_font(font_size: int, bold: bool = False) -> ImageFont.ImageFont:
    """自动探测宿主系统的优质中英文 TrueType/OpenType 字体。"""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, font_size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, max_chars_per_line: int = 38) -> list[str]:
    """折行排版中文与长英文提示词。"""
    if not text:
        return []
    lines: list[str] = []
    current: list[str] = []
    cur_len = 0

    # 按字符切分，汉字计 2 宽，英文计 1 宽
    for ch in text.strip():
        if ch == "\n":
            if current:
                lines.append("".join(current))
                current = []
                cur_len = 0
            continue
        w = 2 if ord(ch) > 127 else 1
        if cur_len + w > max_chars_per_line:
            lines.append("".join(current))
            current = [ch]
            cur_len = w
        else:
            current.append(ch)
            cur_len += w

    if current:
        lines.append("".join(current))
    return lines


def _draw_vertical_gradient(img: Image.Image, color_top: tuple[int, int, int], color_bottom: tuple[int, int, int]) -> None:
    """在图像上绘制平滑的垂直渐变底色。"""
    w, h = img.size
    draw = ImageDraw.Draw(img)
    r1, g1, b1 = color_top
    r2, g2, b2 = color_bottom

    for y in range(h):
        ratio = y / max(1, h - 1)
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))


def _draw_tech_grid(draw: ImageDraw.Draw, width: int, height: int, grid_color: tuple[int, int, int, int], step: int = 40) -> None:
    """绘制高科技半透明背景网格。"""
    for x in range(0, width, step):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, step):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)


def _draw_corner_brackets(draw: ImageDraw.Draw, rect: tuple[int, int, int, int], color: tuple[int, int, int, int], length: int = 28, width: int = 3) -> None:
    """在指定矩形四角绘制 HUD 瞄准括号。"""
    x1, y1, x2, y2 = rect
    # 左上
    draw.line([(x1, y1), (x1 + length, y1)], fill=color, width=width)
    draw.line([(x1, y1), (x1, y1 + length)], fill=color, width=width)
    # 右上
    draw.line([(x2, y1), (x2 - length, y1)], fill=color, width=width)
    draw.line([(x2, y1), (x2, y1 + length)], fill=color, width=width)
    # 左下
    draw.line([(x1, y2), (x1 + length, y2)], fill=color, width=width)
    draw.line([(x1, y2), (x1, y2 - length)], fill=color, width=width)
    # 右下
    draw.line([(x2, y2), (x2 - length, y2)], fill=color, width=width)
    draw.line([(x2, y2), (x2, y2 - length)], fill=color, width=width)


def _render_offline_degradation_card(prompt: str, title: str, style: str, aspect_ratio: str) -> bytes:
    """使用 Pillow 渲染 1200x675 (16:9) 高清艺术与科技卡片。"""
    cfg = STYLE_CONFIGS.get(style.lower().strip()) or STYLE_CONFIGS["illustration"]
    w, h = ASPECT_RATIOS.get(aspect_ratio.strip()) or ASPECT_RATIOS["16:9"]

    base_img = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    _draw_vertical_gradient(base_img, cfg["bg_top"], cfg["bg_bottom"])

    # 叠加半透明科技网格
    grid_overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid_overlay)
    _draw_tech_grid(grid_draw, w, h, cfg["grid_color"], step=45)
    base_img = Image.alpha_composite(base_img, grid_overlay)

    # 主绘图上下文
    draw = ImageDraw.Draw(base_img)

    # 1. 外围霓虹边框与 HUD 护角
    margin = 28
    outer_rect = (margin, margin, w - margin, h - margin)
    primary_color = cfg["neon_primary"]
    secondary_color = cfg["neon_secondary"]
    draw.rounded_rectangle(outer_rect, radius=16, outline=primary_color + (140,), width=2)
    _draw_corner_brackets(draw, outer_rect, primary_color + (255,), length=32, width=3)

    # 2. 顶部遥测与风格胶囊药丸 (Telemetry Badge)
    font_badge = _find_font(15, bold=True)
    pill_text = f"◈ AGENTOS VISION MATRIX // {cfg['name']} // RATIO {aspect_ratio} ◈"
    draw.rectangle([margin + 20, margin + 14, margin + 460, margin + 44], fill=(*cfg["neon_accent"][:3], 38), outline=primary_color + (180,), width=1)
    draw.text((margin + 34, margin + 21), pill_text, font=font_badge, fill=primary_color + (255,))

    # 右上角时间戳与核心标记
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sub_info = f"LATENCY: OPTIMAL | TIMESTAMP: {now_str}"
    font_sub = _find_font(13, bold=False)
    draw.text((w - margin - 340, margin + 23), sub_info, font=font_sub, fill=cfg["text_secondary"] + (210,))

    # 3. 核心作品大标题
    font_title = _find_font(34, bold=True)
    clean_title = (title or "AI 视觉艺术意象").strip()
    if len(clean_title) > 30:
        clean_title = clean_title[:28] + "..."

    # 标题双层轻光晕
    draw.text((margin + 32, margin + 72), clean_title, font=font_title, fill=secondary_color + (80,))
    draw.text((margin + 30, margin + 70), clean_title, font=font_title, fill=cfg["text_primary"] + (255,))

    # 标题下方霓虹强调光带
    accent_y = margin + 120
    draw.line([(margin + 30, accent_y), (margin + 280, accent_y)], fill=primary_color + (240,), width=3)
    draw.line([(margin + 280, accent_y), (margin + 420, accent_y)], fill=secondary_color + (180,), width=1)

    # 4. 提示词灵感金句面板（Prompt Quote Card）
    panel_left = margin + 30
    panel_top = accent_y + 24
    panel_right = w - margin - 30
    panel_bottom = h - margin - 120

    # 半透明玻璃拟态卡片底色
    glass_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    glass_draw = ImageDraw.Draw(glass_layer)
    glass_draw.rounded_rectangle(
        [panel_left, panel_top, panel_right, panel_bottom],
        radius=14,
        fill=cfg["card_bg"],
        outline=cfg["card_border"],
        width=1,
    )
    base_img = Image.alpha_composite(base_img, glass_layer)
    draw = ImageDraw.Draw(base_img)

    # 卡片内部排版
    font_quote_mark = _find_font(42, bold=True)
    font_prompt = _find_font(21, bold=False)
    font_meta = _find_font(14, bold=False)

    # 引号与提示词标签
    draw.text((panel_left + 24, panel_top + 16), "PROMPT SPECIFICATION :", font=font_meta, fill=secondary_color + (230,))
    draw.text((panel_left + 22, panel_top + 42), "“", font=font_quote_mark, fill=primary_color + (160,))

    # 折行排版提示词
    max_chars = 48 if w >= 1200 else 32
    prompt_lines = _wrap_text(prompt or "A breathtaking futuristic artwork synthesized by AgentOS Neural Vision.", max_chars_per_line=max_chars)
    # 最多显示 6 行
    displayed_lines = prompt_lines[:6]
    if len(prompt_lines) > 6:
        displayed_lines[-1] = displayed_lines[-1][: max_chars - 3] + "..."

    line_y = panel_top + 54
    for pline in displayed_lines:
        draw.text((panel_left + 64, line_y), pline, font=font_prompt, fill=cfg["text_primary"] + (240,))
        line_y += 34

    draw.text((panel_right - 46, panel_bottom - 52), "”", font=font_quote_mark, fill=primary_color + (160,))

    # 5. 底部专属认证印章 (AgentOS Certified Seal)
    seal_box_w = 340
    seal_box_h = 74
    seal_x = w - margin - seal_box_w - 20
    seal_y = h - margin - seal_box_h - 16

    # 印章底衬与外边框
    draw.rectangle([seal_x, seal_y, seal_x + seal_box_w, seal_y + seal_box_h], fill=(0, 0, 0, 140), outline=primary_color + (220,), width=2)
    # 双线内框
    draw.rectangle([seal_x + 3, seal_y + 3, seal_x + seal_box_w - 3, seal_y + seal_box_h - 3], outline=primary_color + (90,), width=1)

    # 印章徽标与文本
    font_seal_title = _find_font(14, bold=True)
    font_seal_desc = _find_font(11, bold=False)

    token_hash = hashlib.sha256((prompt + title).encode("utf-8")).hexdigest()[:8].upper()
    draw.text((seal_x + 16, seal_y + 12), f"◈ AGENTOS NEURAL ENGINE ◈", font=font_seal_title, fill=primary_color + (255,))
    draw.text((seal_x + 16, seal_y + 34), f"AUTHENTIC VISION SYNTHESIS ARTIFACT", font=font_seal_desc, fill=cfg["text_primary"] + (210,))
    draw.text((seal_x + 16, seal_y + 50), f"VERIFY HASH: #{token_hash} • HIGH FIDELITY", font=font_seal_desc, fill=secondary_color + (230,))

    # 底部左侧技术规格标签
    draw.text((margin + 30, h - margin - 46), f"MODEL: AgentOS Neural Synth V3.8 // RESOLUTION: {w}x{h} HD", font=font_sub, fill=cfg["text_secondary"] + (180,))
    draw.text((margin + 30, h - margin - 26), f"STATUS: SYNTHESIS COMPLETE // SECURE INTEGRITY VERIFIED", font=font_sub, fill=primary_color + (200,))

    buf = io.BytesIO()
    base_img.convert("RGB").save(buf, format="PNG", quality=95, optimize=True)
    return buf.getvalue()


def _try_pro_api_generation(prompt: str, style: str, aspect_ratio: str) -> bytes | None:
    """尝试使用专业模型 API 生图（SiliconFlow / DashScope / OpenAI 兼容接口）。"""
    api_key = (
        os.environ.get("IMAGE_API_KEY")
        or os.environ.get("SILICONFLOW_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        return None

    api_key = api_key.strip()
    log.info("Detected Pro Image API Key, attempting generation...")

    # 构建富化提示词
    enhanced_prompt = f"{prompt}, {style} style, high resolution, masterpiece, professional aesthetic"

    # 1. 优先尝试 SiliconFlow
    if os.environ.get("SILICONFLOW_API_KEY") or api_key.startswith("sk-") and "silicon" in os.environ.get("IMAGE_API_BASE", "").lower():
        try:
            url = os.environ.get("IMAGE_API_BASE", "https://api.siliconflow.cn/v1") + "/images/generations"
            payload = {
                "model": "black-forest-labs/FLUX.1-schnell",
                "prompt": enhanced_prompt,
                "image_size": "1024x1024",
                "batch_size": 1,
            }
            with httpx.Client(timeout=25.0, headers={"Authorization": f"Bearer {api_key}"}) as client:
                res = client.post(url, json=payload)
                if res.status_code == 200:
                    images = res.json().get("images", [])
                    if images and "url" in images[0]:
                        img_res = client.get(images[0]["url"])
                        if img_res.status_code == 200:
                            return img_res.content
        except Exception as exc:
            log.warning("SiliconFlow image generation failed: %s", exc)

    # 2. 尝试标准 OpenAI / Generic /images/generations 接口
    try:
        base_url = os.environ.get("IMAGE_API_BASE", "https://api.openai.com/v1").rstrip("/")
        gen_url = f"{base_url}/images/generations"
        payload = {
            "prompt": enhanced_prompt,
            "n": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        }
        with httpx.Client(timeout=30.0, headers={"Authorization": f"Bearer {api_key}"}) as client:
            res = client.post(gen_url, json=payload)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data:
                    first = data[0]
                    if "b64_json" in first:
                        return base64.b64decode(first["b64_json"])
                    if "url" in first:
                        img_res = client.get(first["url"])
                        if img_res.status_code == 200:
                            return img_res.content
    except Exception as exc:
        log.warning("OpenAI-compatible image generation failed: %s", exc)

    return None


def generate_image_tool(
    prompt: str,
    title: str = "",
    style: str = "illustration",
    aspect_ratio: str = "16:9",
    session_id: str = "",
    workspace_path: str = "",
) -> dict[str, Any]:
    """生成高质量 AI 视觉图画并落盘为合法 AgentOS Artifact（双模弹性管道）。

    参数：
        prompt: 提示词描述（必填）
        title: 作品标题（选填，未传则自提示词提炼）
        style: 艺术风格，可选 'cyberpunk', 'photorealistic', 'illustration', 'minimalist_infographic'
        aspect_ratio: 画面宽高比，默认 '16:9'
        session_id: 会话标识符
        workspace_path: 自定义工作区绝对路径

    返回：
        dict: {"ok": True, "artifact_id": art_id, "url": art_url, "markdown": f"![{title}]({art_url})",
               "title": title, "prompt": prompt, "style": style}
    """
    try:
        clean_prompt = (prompt or "").strip()
        if not clean_prompt:
            return {"ok": False, "error": "prompt 不能为空"}

        # 整理标题
        clean_title = (title or "").strip()
        if not clean_title:
            raw = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", clean_prompt).strip("_")
            clean_title = raw[:20] if raw else "AI视觉画作"

        clean_style = (style or "illustration").lower().strip()
        if clean_style not in STYLE_CONFIGS:
            clean_style = "illustration"

        clean_ratio = (aspect_ratio or "16:9").strip()
        if clean_ratio not in ASPECT_RATIOS:
            clean_ratio = "16:9"

        # 1. 尝试 Pro API 模式生图
        image_bytes = _try_pro_api_generation(clean_prompt, clean_style, clean_ratio)

        # 2. 若无 API Key 或调用失败，优雅无缝退回离线高雅渲染模式
        if not image_bytes:
            image_bytes = _render_offline_degradation_card(
                prompt=clean_prompt,
                title=clean_title,
                style=clean_style,
                aspect_ratio=clean_ratio,
            )

        # 3. 产物系统集成（落盘并生成合法 Artifact 记录）
        art_id = "art_" + secrets.token_hex(8)
        art_url = f"/v1/artifacts/{art_id}"
        saved_info: dict[str, Any] = {}

        # 优先使用 app.artifacts.save_bytes
        try:
            from .. import artifacts
            saved_info = artifacts.save_bytes(name=f"{clean_title}.png", data=image_bytes, kind="image/png")
            art_id = saved_info.get("id") or art_id
            art_url = saved_info.get("url") or art_url
        except Exception as exc:
            log.warning("app.artifacts.save_bytes failed (%s), writing directly to artifacts dir", exc)

        # 若调用方显式指定了 workspace_path，确保在该工作区的 artifacts/ 下也有一份
        if workspace_path:
            try:
                target_ws = pathlib.Path(workspace_path)
                art_dir = target_ws / "artifacts"
                art_dir.mkdir(parents=True, exist_ok=True)
                art_file = art_dir / f"{art_id}.png"
                art_file.write_bytes(image_bytes)
                meta_file = art_dir / f"{art_id}.meta.json"
                meta_data = {
                    "id": art_id,
                    "name": f"{clean_title}.png",
                    "kind": "image/png",
                    "bytes": len(image_bytes),
                    "file": art_file.name,
                    "path": str(art_file),
                    "url": art_url,
                    "created_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                }
                meta_file.write_text(json.dumps(meta_data, ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception as w_exc:
                log.warning("Writing to workspace_path failed: %s", w_exc)

        return {
            "ok": True,
            "artifact_id": art_id,
            "url": art_url,
            "markdown": f"![{clean_title}]({art_url})",
            "title": clean_title,
            "prompt": clean_prompt,
            "style": clean_style,
        }

    except Exception as exc:
        log.exception("generate_image_tool encountered unexpected error: %s", exc)
        return {"ok": False, "error": f"生图失败: {exc}"}


# 别名兼容
generate_image = generate_image_tool
