"""Code and engineering symbol outline & semantic indexing engine for AgentOS.

Provides lightweight, token-efficient code intelligence tools:
- code_outline: Structural symbol outline (AST for Python, regex for JS/TS/Go/Java/Rust/C/C++)
  with start/end line ranges, decorators, signatures, and docstrings.
- code_symbols: Recursive workspace symbol index with incremental .code_index.json caching
  and fuzzy symbol querying.
- code_references: Symbol call site, import, and definition cross-reference locator.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import pathlib
import re
import time
from typing import Any

log = logging.getLogger("app.agent.code_tools")

IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    "ui_dist",
    ".tool_outputs",
}

SOURCE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "typescriptreact",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
}


# -----------------------------------------------------------------------------
# Path and workspace utilities
# -----------------------------------------------------------------------------

def _get_workspace_root() -> pathlib.Path:
    try:
        from . import tools
        ws = pathlib.Path(tools.WORKSPACE).resolve()
        if ws.exists():
            return ws
    except Exception:
        pass
    env_ws = os.environ.get("M0_WORKSPACE")
    if env_ws and pathlib.Path(env_ws).exists():
        return pathlib.Path(env_ws).resolve()
    return pathlib.Path.cwd().resolve()


def _resolve_target(path: str | None) -> pathlib.Path:
    """Resolve a user-provided file/dir path safely against WORKSPACE or current working directory."""
    raw = (path or ".").strip()
    if not raw:
        raw = "."

    p = pathlib.Path(raw)
    if p.is_absolute():
        return p.resolve()

    # Try tools._jail if target exists inside WORKSPACE
    try:
        from . import tools
        jailed = tools._jail(raw)
        if jailed.exists() and (jailed != tools.WORKSPACE or any(jailed.iterdir())):
            return jailed
    except Exception:
        pass

    # If target exists relative to current working directory (e.g. repo workspace)
    cwd_target = (pathlib.Path.cwd() / raw).resolve()
    if cwd_target.exists():
        return cwd_target

    # Default fallback
    try:
        from . import tools
        return tools._jail(raw)
    except Exception:
        return (_get_workspace_root() / raw).resolve()


def _rel_path(target: pathlib.Path, base: pathlib.Path | None = None) -> str:
    root = (base or _get_workspace_root()).resolve()
    try:
        return str(target.resolve().relative_to(root)).replace("\\", "/")
    except Exception:
        pass
    try:
        cwd = pathlib.Path.cwd().resolve()
        return str(target.resolve().relative_to(cwd)).replace("\\", "/")
    except Exception:
        return str(target.resolve()).replace("\\", "/")


def _safe_read_text(path: pathlib.Path) -> tuple[str, list[str]]:
    """Read file content with UTF-8 / GB18030 decoding tolerance."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            text = raw.decode(enc)
            return text, text.splitlines()
        except (UnicodeDecodeError, LookupError):
            continue
    text = raw.decode("utf-8", errors="replace")
    return text, text.splitlines()


# -----------------------------------------------------------------------------
# Line range & comment helpers for regex parsers
# -----------------------------------------------------------------------------

def _find_matching_brace_end(lines: list[str], start_line_1idx: int) -> int:
    """Find closing brace end line for C-style brace languages (JS/TS, Go, Java, Rust, C/C++)."""
    idx = max(0, start_line_1idx - 1)
    depth = 0
    found_open = False

    for i in range(idx, min(len(lines), idx + 2500)):
        line = lines[i]
        # Strip out string literals and comments to avoid counting braces inside them
        clean = re.sub(
            r'//.*$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`',
            '',
            line,
        )
        for char in clean:
            if char == '{':
                depth += 1
                found_open = True
            elif char == '}':
                depth -= 1
                if found_open and depth <= 0:
                    return i + 1

    return start_line_1idx


def _extract_preceding_doc(lines: list[str], start_line_1idx: int) -> str:
    """Extract first line of doc comment immediately preceding a symbol definition."""
    idx = max(0, start_line_1idx - 1) - 1
    # Skip any preceding decorator/annotation lines
    while idx >= 0 and lines[idx].strip().startswith("@"):
        idx -= 1

    if idx < 0:
        return ""

    line = lines[idx].strip()

    # Triple slash ///, //! or // or #
    if line.startswith(("//", "///", "//!")):
        block = []
        cur = idx
        while cur >= 0 and lines[cur].strip().startswith(("//", "///", "//!")):
            cleaned = re.sub(r'^(?:///|//!|//)\s*', '', lines[cur].strip())
            if cleaned:
                block.append(cleaned)
            cur -= 1
        block.reverse()
        return block[0] if block else ""

    if line.startswith("#"):
        block = []
        cur = idx
        while cur >= 0 and lines[cur].strip().startswith("#"):
            cleaned = re.sub(r'^#\s*', '', lines[cur].strip())
            if cleaned:
                block.append(cleaned)
            cur -= 1
        block.reverse()
        return block[0] if block else ""

    # Multi-line /* ... */
    if line.endswith("*/"):
        block = []
        cur = idx
        while cur >= 0:
            cleaned = re.sub(r'^/\*\*?|^\*+|\*/$', '', lines[cur].strip()).strip()
            if cleaned:
                block.append(cleaned)
            if "/*" in lines[cur]:
                break
            cur -= 1
        block.reverse()
        return block[0] if block else ""

    return ""


def _extract_preceding_decorators(lines: list[str], start_line_1idx: int) -> list[str]:
    """Extract decorator annotations (@decorator) immediately preceding a symbol."""
    decs = []
    idx = max(0, start_line_1idx - 1) - 1
    while idx >= 0:
        l = lines[idx].strip()
        if l.startswith("@"):
            decs.append(l)
            idx -= 1
        else:
            break
    decs.reverse()
    return decs


# -----------------------------------------------------------------------------
# Python AST Outline Parser
# -----------------------------------------------------------------------------

def _extract_py_docstring(node: ast.AST) -> str:
    doc = ast.get_docstring(node)
    if not doc:
        return ""
    lines = [l.strip() for l in doc.strip().splitlines() if l.strip()]
    return lines[0] if lines else ""


def _format_py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
    try:
        args_str = ast.unparse(node.args)
    except Exception:
        args_str = "..."
    ret_str = ""
    if getattr(node, "returns", None):
        try:
            ret_str = f" -> {ast.unparse(node.returns)}"
        except Exception:
            ret_str = ""
    return f"{prefix}{node.name}({args_str}){ret_str}"


def _format_py_class_signature(node: ast.ClassDef) -> str:
    bases_str = ""
    if node.bases:
        try:
            bases_str = f"({', '.join(ast.unparse(b) for b in node.bases)})"
        except Exception:
            bases_str = "(...)"
    return f"class {node.name}{bases_str}"


def _parse_python_ast(code: str, max_depth: int = 2) -> list[dict[str, Any]]:
    tree = ast.parse(code)

    def _parse_body(body_nodes: list[ast.stmt], current_depth: int, in_class: bool = False) -> list[dict[str, Any]]:
        symbols: list[dict[str, Any]] = []
        for node in body_nodes:
            if isinstance(node, ast.ClassDef):
                doc = _extract_py_docstring(node)
                decorators = []
                for d in node.decorator_list:
                    try:
                        decorators.append(f"@{ast.unparse(d)}")
                    except Exception:
                        decorators.append("@...")
                sig = _format_py_class_signature(node)
                children = []
                if current_depth < max_depth:
                    children = _parse_body(node.body, current_depth + 1, in_class=True)

                symbols.append({
                    "name": node.name,
                    "kind": "class",
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "signature": sig,
                    "decorators": decorators,
                    "docstring": doc,
                    "children": children,
                })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_async = isinstance(node, ast.AsyncFunctionDef)
                doc = _extract_py_docstring(node)
                decorators = []
                for d in node.decorator_list:
                    try:
                        decorators.append(f"@{ast.unparse(d)}")
                    except Exception:
                        decorators.append("@...")
                sig = _format_py_signature(node)
                children = []
                if current_depth < max_depth:
                    children = _parse_body(node.body, current_depth + 1, in_class=False)

                kind = "method" if in_class else ("async_function" if is_async else "function")
                symbols.append({
                    "name": node.name,
                    "kind": kind,
                    "is_async": is_async,
                    "start_line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "signature": sig,
                    "decorators": decorators,
                    "docstring": doc,
                    "children": children,
                })
        return symbols

    return _parse_body(tree.body, current_depth=1, in_class=False)


# -----------------------------------------------------------------------------
# Regex Outline Parsers for Polyglot Codebases
# -----------------------------------------------------------------------------

def _parse_python_regex(lines: list[str], max_depth: int = 2) -> list[dict[str, Any]]:
    """Resilient fallback regex parser when Python file contains syntax errors."""
    symbols: list[dict[str, Any]] = []
    class_pattern = re.compile(r'^(\s*)class\s+(\w+)(?:\((.*?)\))?:')
    func_pattern = re.compile(r'^(\s*)(async\s+)?def\s+(\w+)\s*\((.*?)\)(?:\s*->\s*(.*?))?:')

    for idx, line in enumerate(lines):
        line_num = idx + 1
        m_cls = class_pattern.match(line)
        if m_cls:
            indent, name, bases = m_cls.group(1), m_cls.group(2), m_cls.group(3)
            bases_str = f"({bases.strip()})" if bases and bases.strip() else ""
            decs = _extract_preceding_decorators(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "class",
                "start_line": line_num,
                "end_line": line_num,
                "signature": f"class {name}{bases_str}",
                "decorators": decs,
                "docstring": doc,
                "children": [],
            })
            continue

        m_fn = func_pattern.match(line)
        if m_fn:
            indent, async_kw, name, args, ret = m_fn.groups()
            is_async = bool(async_kw)
            ret_str = f" -> {ret.strip()}" if ret and ret.strip() else ""
            prefix = "async def " if is_async else "def "
            decs = _extract_preceding_decorators(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            kind = "method" if len(indent or "") > 0 else ("async_function" if is_async else "function")
            symbols.append({
                "name": name,
                "kind": kind,
                "is_async": is_async,
                "start_line": line_num,
                "end_line": line_num,
                "signature": f"{prefix}{name}({args.strip()}){ret_str}",
                "decorators": decs,
                "docstring": doc,
                "children": [],
            })

    return symbols


def _parse_js_ts(lines: list[str], max_depth: int = 2) -> list[dict[str, Any]]:
    """Parse JavaScript and TypeScript files using precision regex."""
    symbols: list[dict[str, Any]] = []

    cls_re = re.compile(
        r'^\s*(?:export\s+(?:default\s+)?)?class\s+([A-Za-z0-9_$]+)(?:\s+extends\s+[A-Za-z0-9_$.<>]+)?(?:\s+implements\s+[^{]+)?\s*\{'
    )
    iface_re = re.compile(
        r'^\s*(?:export\s+)?interface\s+([A-Za-z0-9_$]+)(?:\s+extends\s+[^{]+)?\s*\{'
    )
    type_re = re.compile(
        r'^\s*(?:export\s+)?type\s+([A-Za-z0-9_$]+)(?:<[^>]+>)?\s*=\s*'
    )
    fn_re = re.compile(
        r'^\s*(?:export\s+(?:default\s+)?)?(async\s+)?function(?:\s*\*|\s+)([A-Za-z0-9_$]+)\s*\(([^)]*)\)'
    )
    arrow_re = re.compile(
        r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_$]+)\s*=\s*(async\s*)?(?:\([^)]*\)|[A-Za-z0-9_$]+)\s*=>'
    )
    method_re = re.compile(
        r'^\s*(?:(?:public|private|protected|static|readonly|override)\s+)*(async\s+)?([A-Za-z0-9_$]+)\s*\(([^)]*)\)\s*(?::\s*[^{]+)?\s*\{'
    )

    keyword_exclude = {"if", "while", "for", "switch", "catch", "return", "function", "constructor"}

    for idx, line in enumerate(lines):
        line_num = idx + 1
        clean_line = line.strip()

        # Class
        m_cls = cls_re.match(line)
        if m_cls:
            name = m_cls.group(1)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            decs = _extract_preceding_decorators(lines, line_num)
            children: list[dict[str, Any]] = []

            # Extract methods inside class if max_depth >= 2
            if max_depth >= 2 and end_line > line_num:
                sub_lines = lines[line_num:end_line - 1]
                for sub_idx, sub_l in enumerate(sub_lines):
                    m_m = method_re.match(sub_l)
                    if m_m:
                        m_name = m_m.group(2)
                        if m_name in keyword_exclude:
                            continue
                        m_async = bool(m_m.group(1))
                        m_line = line_num + 1 + sub_idx
                        m_end = _find_matching_brace_end(lines, m_line)
                        m_doc = _extract_preceding_doc(lines, m_line)
                        m_decs = _extract_preceding_decorators(lines, m_line)
                        m_sig = sub_l.strip().rstrip("{").strip()
                        children.append({
                            "name": m_name,
                            "kind": "method",
                            "is_async": m_async,
                            "start_line": m_line,
                            "end_line": m_end,
                            "signature": m_sig,
                            "decorators": m_decs,
                            "docstring": m_doc,
                            "children": [],
                        })

            sig = clean_line.rstrip("{").strip()
            symbols.append({
                "name": name,
                "kind": "class",
                "start_line": line_num,
                "end_line": end_line,
                "signature": sig,
                "decorators": decs,
                "docstring": doc,
                "children": children,
            })
            continue

        # Interface
        m_iface = iface_re.match(line)
        if m_iface:
            name = m_iface.group(1)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "interface",
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean_line.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        # Type alias
        m_type = type_re.match(line)
        if m_type:
            name = m_type.group(1)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "type",
                "start_line": line_num,
                "end_line": line_num,
                "signature": clean_line.rstrip(";").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        # Function
        m_fn = fn_re.match(line)
        if m_fn:
            is_async = bool(m_fn.group(1))
            name = m_fn.group(2)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "async_function" if is_async else "function",
                "is_async": is_async,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean_line.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        # Arrow function assigned to variable
        m_arrow = arrow_re.match(line)
        if m_arrow:
            name = m_arrow.group(1)
            is_async = bool(m_arrow.group(2))
            end_line = _find_matching_brace_end(lines, line_num) if "{" in line else line_num
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "async_function" if is_async else "function",
                "is_async": is_async,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean_line.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

    return symbols


def _parse_go(lines: list[str]) -> list[dict[str, Any]]:
    """Parse Go structs, interfaces, functions and receiver methods."""
    symbols: list[dict[str, Any]] = []

    type_re = re.compile(r'^type\s+([A-Za-z0-9_]+)\s+(struct|interface)\b')
    method_re = re.compile(r'^func\s*\(\s*(?:[A-Za-z0-9_]+\s+)?\*?([A-Za-z0-9_]+)\s*\)\s*([A-Za-z0-9_]+)\s*\(([^)]*)\)')
    func_re = re.compile(r'^func\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)')

    for idx, line in enumerate(lines):
        line_num = idx + 1
        clean = line.strip()

        m_type = type_re.match(line)
        if m_type:
            name, kind = m_type.group(1), m_type.group(2)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": kind,
                "start_line": line_num,
                "end_line": end_line,
                "signature": f"type {name} {kind}",
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        m_method = method_re.match(line)
        if m_method:
            receiver, name, args = m_method.group(1), m_method.group(2), m_method.group(3)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "receiver": receiver,
                "kind": "method",
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        m_fn = func_re.match(line)
        if m_fn:
            name, args = m_fn.group(1), m_fn.group(2)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "function",
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

    return symbols


def _parse_java(lines: list[str]) -> list[dict[str, Any]]:
    """Parse Java classes, interfaces, enums, records and methods."""
    symbols: list[dict[str, Any]] = []

    cls_re = re.compile(
        r'^\s*(?:(?:public|protected|private|static|abstract|final)\s+)*(class|interface|enum|record)\s+([A-Za-z0-9_]+)'
    )
    method_re = re.compile(
        r'^\s*(?:(?:public|protected|private|static|final|abstract|synchronized)\s+)*([A-Za-z0-9_<>\[\],\s]+)\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*(?:throws\s+[^{]+)?\s*\{'
    )
    keyword_exclude = {"if", "while", "for", "switch", "catch", "return", "new"}

    for idx, line in enumerate(lines):
        line_num = idx + 1
        clean = line.strip()

        m_cls = cls_re.match(line)
        if m_cls:
            kind, name = m_cls.group(1), m_cls.group(2)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": kind,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": _extract_preceding_decorators(lines, line_num),
                "docstring": doc,
                "children": [],
            })
            continue

        m_method = method_re.match(line)
        if m_method:
            name = m_method.group(2)
            if name in keyword_exclude:
                continue
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "method",
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": _extract_preceding_decorators(lines, line_num),
                "docstring": doc,
                "children": [],
            })

    return symbols


def _parse_rust(lines: list[str]) -> list[dict[str, Any]]:
    """Parse Rust structs, enums, traits, functions and impl methods."""
    symbols: list[dict[str, Any]] = []

    item_re = re.compile(r'^\s*(?:pub(?:\([^)]+\))?\s+)?(struct|enum|trait|type)\s+([A-Za-z0-9_]+)')
    fn_re = re.compile(r'^\s*(?:pub(?:\([^)]+\))?\s+)?(async\s+)?fn\s+([A-Za-z0-9_]+)\s*(?:<[^>]+>)?\s*\(([^)]*)\)')

    for idx, line in enumerate(lines):
        line_num = idx + 1
        clean = line.strip()

        m_item = item_re.match(line)
        if m_item:
            kind, name = m_item.group(1), m_item.group(2)
            end_line = _find_matching_brace_end(lines, line_num) if "{" in line else line_num
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": kind,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{;").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        m_fn = fn_re.match(line)
        if m_fn:
            is_async = bool(m_fn.group(1))
            name = m_fn.group(2)
            end_line = _find_matching_brace_end(lines, line_num) if "{" in line else line_num
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "async_function" if is_async else "function",
                "is_async": is_async,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

    return symbols


def _parse_c_cpp(lines: list[str]) -> list[dict[str, Any]]:
    """Parse C/C++ classes, structs and functions."""
    symbols: list[dict[str, Any]] = []

    cls_re = re.compile(r'^\s*(class|struct)\s+([A-Za-z0-9_]+)(?:\s*:\s*[^{]+)?\s*\{')
    fn_re = re.compile(r'^\s*(?:[A-Za-z0-9_:*&<>]+\s+)+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*(?:const)?\s*(?:noexcept)?\s*\{')
    keyword_exclude = {"if", "while", "for", "switch", "catch", "return"}

    for idx, line in enumerate(lines):
        line_num = idx + 1
        clean = line.strip()

        m_cls = cls_re.match(line)
        if m_cls:
            kind, name = m_cls.group(1), m_cls.group(2)
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": kind,
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })
            continue

        m_fn = fn_re.match(line)
        if m_fn:
            name = m_fn.group(1)
            if name in keyword_exclude:
                continue
            end_line = _find_matching_brace_end(lines, line_num)
            doc = _extract_preceding_doc(lines, line_num)
            symbols.append({
                "name": name,
                "kind": "function",
                "start_line": line_num,
                "end_line": end_line,
                "signature": clean.rstrip("{").strip(),
                "decorators": [],
                "docstring": doc,
                "children": [],
            })

    return symbols


# -----------------------------------------------------------------------------
# Generic Symbol Extraction Router
# -----------------------------------------------------------------------------

def extract_file_symbols(target_path: pathlib.Path, max_depth: int = 2) -> tuple[str, list[dict[str, Any]], int]:
    """Extract symbol tree from target file, returning (language, symbols, total_lines)."""
    text, lines = _safe_read_text(target_path)
    ext = target_path.suffix.lower()
    lang = SOURCE_EXTENSIONS.get(ext, "unknown")
    total_lines = len(lines)

    if lang == "python":
        try:
            symbols = _parse_python_ast(text, max_depth=max_depth)
        except SyntaxError:
            log.warning("AST parse syntax error in %s, falling back to regex", target_path)
            symbols = _parse_python_regex(lines, max_depth=max_depth)
        except Exception as e:
            log.warning("AST parse error in %s: %s, falling back to regex", target_path, e)
            symbols = _parse_python_regex(lines, max_depth=max_depth)
    elif lang in ("javascript", "typescript", "javascriptreact", "typescriptreact"):
        symbols = _parse_js_ts(lines, max_depth=max_depth)
    elif lang == "go":
        symbols = _parse_go(lines)
    elif lang == "java":
        symbols = _parse_java(lines)
    elif lang == "rust":
        symbols = _parse_rust(lines)
    elif lang in ("c", "cpp"):
        symbols = _parse_c_cpp(lines)
    else:
        # Fallback regex for other text/code formats
        symbols = _parse_python_regex(lines, max_depth=max_depth)

    return lang, symbols, total_lines


def _format_markdown_outline(rel_path: str, lang: str, total_lines: int, symbols: list[dict[str, Any]]) -> str:
    """Format extracted symbol hierarchy into clean, token-efficient Markdown outline."""
    out: list[str] = [f"# Outline: `{rel_path}` ({total_lines} lines, {lang})", ""]

    def _render(sym: dict[str, Any], indent: int):
        prefix = "  " * indent + "- "
        sig = sym.get("signature") or f"{sym.get('kind', 'symbol')} {sym.get('name', '')}"
        start_line = sym.get("start_line", 1)
        end_line = sym.get("end_line", start_line)
        line_tag = f"[L{start_line}-L{end_line}]" if end_line != start_line else f"[L{start_line}]"

        decs = sym.get("decorators") or []
        dec_tag = f" ({', '.join(decs)})" if decs else ""

        out.append(f"{prefix}`{sig}`{dec_tag} {line_tag}")

        doc = sym.get("docstring")
        if doc:
            out.append(f"{'  ' * (indent + 1)}*\"{doc}\"*")

        for child in sym.get("children", []):
            _render(child, indent + 1)

    if not symbols:
        out.append("*(No symbols found)*")
    else:
        for s in symbols:
            _render(s, 0)

    return "\n".join(out)


# -----------------------------------------------------------------------------
# Core Tool 1: code_outline
# -----------------------------------------------------------------------------

def code_outline(path: str, max_depth: int = 2) -> dict[str, Any]:
    """提取单个源码文件的高密度类/函数符号大纲、起止行号与文档注释。

    Args:
        path: 相对工作区或目标文件路径。
        max_depth: 提取层级深度（默认 2：展开顶级类与内部方法）。

    Returns:
        包含 markdown 大纲与 symbols 结构化 JSON 的字典。
    """
    if not path or not str(path).strip():
        return {"ok": False, "error": "path 不能为空"}

    try:
        target = _resolve_target(path)
    except Exception as e:
        return {"ok": False, "error": f"Invalid path {path}: {e}"}

    if not target.exists():
        return {"ok": False, "error": f"File not found: {path}"}
    if not target.is_file():
        return {"ok": False, "error": f"Not a regular file: {path}"}

    rel = _rel_path(target)
    try:
        lang, symbols, total_lines = extract_file_symbols(target, max_depth=max(1, int(max_depth or 2)))
        md = _format_markdown_outline(rel, lang, total_lines, symbols)
        return {
            "ok": True,
            "path": rel,
            "language": lang,
            "total_lines": total_lines,
            "symbols": symbols,
            "outline": md,
            "markdown": md,
        }
    except Exception as e:
        log.exception("code_outline failed on %s", path)
        return {"ok": False, "error": f"Failed to parse outline for {path}: {e}"}


# -----------------------------------------------------------------------------
# Flattening symbols for global indexing & querying
# -----------------------------------------------------------------------------

def _flatten_symbols(symbols: list[dict[str, Any]], rel_path: str, parent_name: str = "") -> list[dict[str, Any]]:
    """Flatten hierarchical symbols into a list of queryable symbol entries."""
    flat: list[dict[str, Any]] = []
    for s in symbols:
        name = s.get("name", "")
        full_name = f"{parent_name}.{name}" if parent_name else name
        flat.append({
            "name": name,
            "full_name": full_name,
            "kind": s.get("kind", "symbol"),
            "path": rel_path,
            "line": s.get("start_line", 1),
            "end_line": s.get("end_line", s.get("start_line", 1)),
            "signature": s.get("signature", ""),
            "docstring": s.get("docstring", ""),
        })
        children = s.get("children", [])
        if children:
            flat.extend(_flatten_symbols(children, rel_path, parent_name=full_name))
    return flat


# -----------------------------------------------------------------------------
# Core Tool 2: code_symbols with .code_index.json caching
# -----------------------------------------------------------------------------

def _load_cache(cache_file: pathlib.Path) -> dict[str, Any]:
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": 1, "updated_at": 0, "files": {}}


def _save_cache(cache_file: pathlib.Path, data: dict[str, Any]) -> None:
    try:
        data["updated_at"] = time.time()
        temp_file = cache_file.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(cache_file)
    except Exception as e:
        log.warning("Failed to save .code_index.json: %s", e)


def code_symbols(
    query: str = "",
    path: str = ".",
    symbol_type: str = "all",
    max_results: int = 50,
) -> dict[str, Any]:
    """递归扫描工程源码符号，更新轻量级 .code_index.json 缓存，并支持模糊匹配。

    Args:
        query: 符号搜索词（如 _compact_context、SessionBlackboard、render_chart）。为空时列出全部。
        path: 相对工作区的起始扫描目录，默认 .。
        symbol_type: 符号类型过滤（all / class / function / method / struct / interface），默认 all。
        max_results: 最大返回数量限制，默认 50 条。
    """
    try:
        base_dir = _resolve_target(path)
    except Exception as e:
        return {"ok": False, "error": f"Invalid path {path}: {e}"}

    if not base_dir.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}

    workspace_root = _get_workspace_root()
    cache_file = workspace_root / ".code_index.json"
    cache_data = _load_cache(cache_file)
    cached_files = cache_data.setdefault("files", {})

    all_symbols: list[dict[str, Any]] = []
    cache_dirty = False
    files_scanned = 0

    # Collect source files
    target_files: list[pathlib.Path] = []
    if base_dir.is_file():
        target_files = [base_dir]
    else:
        for root_dir, dirs, filenames in os.walk(str(base_dir)):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in filenames:
                p = pathlib.Path(root_dir) / f
                if p.suffix.lower() in SOURCE_EXTENSIONS:
                    target_files.append(p)

    # Process and build index
    for file_p in target_files:
        files_scanned += 1
        rel = _rel_path(file_p, workspace_root)
        try:
            mtime = file_p.stat().st_mtime
        except OSError:
            continue

        cached_entry = cached_files.get(rel)
        if cached_entry and abs(cached_entry.get("mtime", 0) - mtime) < 0.001:
            file_symbols = cached_entry.get("symbols", [])
        else:
            try:
                _, raw_symbols, _ = extract_file_symbols(file_p, max_depth=2)
                file_symbols = _flatten_symbols(raw_symbols, rel)
                cached_files[rel] = {"mtime": mtime, "symbols": file_symbols}
                cache_dirty = True
            except Exception as e:
                log.debug("Skip indexing %s: %s", rel, e)
                file_symbols = []

        all_symbols.extend(file_symbols)

    if cache_dirty:
        _save_cache(cache_file, cache_data)

    # Filter by symbol_type
    target_st = (symbol_type or "all").lower().strip()
    if target_st != "all":
        if target_st in ("func", "function"):
            allowed = {"function", "async_function", "method"}
        elif target_st in ("class", "struct", "interface"):
            allowed = {"class", "struct", "interface", "trait", "enum", "type"}
        else:
            allowed = {target_st}
        all_symbols = [s for s in all_symbols if s.get("kind", "").lower() in allowed]

    # Filter and rank by query
    q = (query or "").strip().lower()
    matched: list[dict[str, Any]] = []

    if not q:
        matched = all_symbols
    else:
        scored: list[tuple[int, dict[str, Any]]] = []
        for sym in all_symbols:
            name_lower = sym.get("name", "").lower()
            full_lower = sym.get("full_name", "").lower()
            sig_lower = sym.get("signature", "").lower()

            score = 0
            if name_lower == q:
                score = 100
            elif full_lower == q:
                score = 95
            elif name_lower.startswith(q):
                score = 80
            elif full_lower.startswith(q):
                score = 75
            elif q in name_lower:
                score = 60
            elif q in full_lower:
                score = 50
            elif q in sig_lower:
                score = 30

            if score > 0:
                scored.append((score, sym))

        # Sort by score DESC, then by file path, then by line
        scored.sort(key=lambda item: (-item[0], item[1].get("path", ""), item[1].get("line", 0)))
        matched = [item[1] for item in scored]

    limit = max(1, min(int(max_results or 50), 300))
    results = matched[:limit]

    return {
        "ok": True,
        "query": query,
        "symbol_type": symbol_type,
        "files_scanned": files_scanned,
        "total_symbols": len(all_symbols),
        "count": len(results),
        "symbols": results,
        "cache_file": _rel_path(cache_file, workspace_root),
    }


# -----------------------------------------------------------------------------
# Core Tool 3: code_references
# -----------------------------------------------------------------------------

def code_references(
    symbol: str,
    path: str = ".",
    max_results: int = 40,
) -> dict[str, Any]:
    """检索指定符号在工程源码中的所有导入（import）、定义（def）与调用点引用。

    Args:
        symbol: 要检索的符号名称（例如 SessionBlackboard、_compact_context）。
        path: 相对工作区的起始搜索目录，默认 .。
        max_results: 最大返回引用条数，默认 40 条。
    """
    sym = (symbol or "").strip()
    if not sym:
        return {"ok": False, "error": "symbol 不能为空"}

    try:
        base_dir = _resolve_target(path)
    except Exception as e:
        return {"ok": False, "error": f"Invalid path {path}: {e}"}

    if not base_dir.exists():
        return {"ok": False, "error": f"Path not found: {path}"}

    workspace_root = _get_workspace_root()
    limit = max(1, min(int(max_results or 40), 200))

    try:
        word_re = re.compile(r'\b' + re.escape(sym) + r'\b')
        call_re = re.compile(r'\b' + re.escape(sym) + r'\s*\(')
        import_re = re.compile(
            r'^\s*(?:from\s+[\w.]+\s+import\s+.*?\b' + re.escape(sym) + r'\b'
            r'|import\s+.*?\b' + re.escape(sym) + r'\b'
            r'|const\s+.*?=\s*require\b'
            r'|import\s+.*\{[^}]*\b' + re.escape(sym) + r'\b)'
        )
        def_re = re.compile(
            r'^\s*(?:(?:async\s+)?def|class|interface|type|struct|func(?:\s*\([^)]*\))?|fn)\s+'
            + re.escape(sym) + r'\b'
        )
    except re.error as e:
        return {"ok": False, "error": f"Invalid symbol regex: {e}"}

    # Collect source files
    target_files: list[pathlib.Path] = []
    if base_dir.is_file():
        target_files = [base_dir]
    else:
        for root_dir, dirs, filenames in os.walk(str(base_dir)):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in filenames:
                p = pathlib.Path(root_dir) / f
                if p.suffix.lower() in SOURCE_EXTENSIONS:
                    target_files.append(p)

    references: list[dict[str, Any]] = []

    for file_p in target_files:
        rel = _rel_path(file_p, workspace_root)
        try:
            _, lines = _safe_read_text(file_p)
        except Exception:
            continue

        for idx, line in enumerate(lines):
            if not word_re.search(line):
                continue

            line_num = idx + 1
            clean_line = line.strip()

            # Classify kind
            if import_re.search(line):
                kind = "import"
            elif def_re.search(line):
                kind = "def"
            elif call_re.search(line):
                kind = "call"
            else:
                kind = "usage"

            references.append({
                "path": rel,
                "line": line_num,
                "kind": kind,
                "line_text": clean_line,
            })

            if len(references) >= limit:
                break

        if len(references) >= limit:
            break

    return {
        "ok": True,
        "symbol": sym,
        "count": len(references),
        "references": references,
    }
