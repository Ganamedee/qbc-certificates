"""What every live board shares: how long the public top list is, and how places are written."""

from __future__ import annotations

TOP = 10


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def names(entries: list) -> str:
    return ", ".join(e["author"] for e in entries) or "nobody yet"
