"""Canonical file identity for console artifact lists and soft deletion."""
from __future__ import annotations

import os
import pathlib


def path_key(path: str) -> str:
    try:
        resolved = str(pathlib.Path(path).resolve())
    except (OSError, RuntimeError):
        resolved = os.path.abspath(os.path.normpath(path))
    return os.path.normcase(resolved)


def unique_paths(rows) -> list[dict]:
    """Keep the first version of each path; callers order newest versions first."""
    out, seen = [], set()
    for row in rows:
        key = path_key(row["file_path"])
        if key not in seen:
            seen.add(key)
            out.append(dict(row))
    return out
