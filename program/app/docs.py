"""Best-effort text extraction for the knowledge base.

PDF: prefer pypdf if present, else fall back to a crude latin/UTF-8 scrape.
DOCX: a .docx is a zip — read word/document.xml and strip tags.
"""
from __future__ import annotations

import re
import zipfile


def pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader          # type: ignore
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages[:20])
    except Exception:                        # noqa: BLE001
        raw = open(path, "rb").read()
        text = raw.decode("latin-1", errors="ignore")
        chunks = re.findall(r"\((.*?)\)\s*Tj", text)
        return "\n".join(chunks)[:20000] or "(PDF 已存，未能抽取文本)"


def docx_text(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as exc:                 # noqa: BLE001
        return f"(无法解析 docx：{exc})"
    xml = xml.replace("</w:p>", "\n")
    return re.sub(r"<[^>]+>", "", xml)[:20000]
