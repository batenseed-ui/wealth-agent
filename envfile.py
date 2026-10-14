"""Minimal .env loader (no extra packages needed).

A .env file holds KEY=VALUE lines, for example:

    GEMINI_API_KEY=your-key-here
    GEMINI_MODEL=some-model-name
    # lines starting with # are comments

Values already set in the real environment always win, so you can still override
a .env value for one run with  $env:GEMINI_MODEL="other"  in PowerShell.
"""

from __future__ import annotations

import os


def parse_env_text(text: str) -> dict:
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:  # trailing comment on an unquoted value
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_env_file(path: str) -> list:
    """Load a .env file into os.environ. Returns the names it set. Missing file is fine."""
    try:
        with open(path, encoding="utf-8-sig") as fh:  # utf-8-sig tolerates Windows Notepad's BOM
            text = fh.read()
    except OSError:
        return []
    loaded = []
    for key, value in parse_env_text(text).items():
        if value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
