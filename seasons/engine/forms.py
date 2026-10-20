"""Reads the body of an issue opened with a GitHub issue form.

GitHub writes each field as a "### Label" heading followed by its value. A textarea with `render` set is wrapped
in a code fence, an empty field is "_No response_", and ticked boxes are "- [X] label".
"""

from __future__ import annotations

import re

NO_RESPONSE = "_No response_"
_FENCE = re.compile(r"^\s*(```|~~~)")


def fields(body: str) -> dict:
    """{label: value} for every "### Label" section, in order. Headings inside code fences are values."""
    out: dict = {}
    label, lines, fence = None, [], None
    for line in (body or "").replace("\r\n", "\n").split("\n"):
        m = _FENCE.match(line)
        if m:
            fence = None if fence == m.group(1) else (fence or m.group(1))
        if fence is None and line.startswith("### ") and not m:
            if label is not None:
                out[label] = _value(lines)
            label, lines = line[4:].strip(), []
        elif label is not None:
            lines.append(line)
    if label is not None:
        out[label] = _value(lines)
    return out


def _value(lines: list) -> str:
    text = "\n".join(lines).strip()
    if text == NO_RESPONSE:
        return ""
    fenced = re.fullmatch(r"(```|~~~)[^\n]*\n(.*?)\n?\1", text, re.S)
    return fenced.group(2).strip() if fenced else text


def ticked(value: str, option: str) -> bool:
    return any(line.strip().lower() == f"- [x] {option}".lower() for line in value.split("\n"))
