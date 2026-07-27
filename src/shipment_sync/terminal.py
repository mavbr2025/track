from __future__ import annotations

from typing import Any


def terminal_safe_text(value: Any) -> str:
    """Render dynamic terminal text without emitting terminal control bytes."""
    if value is None:
        return ""
    text = str(value)
    rendered: list[str] = []
    for character in text:
        codepoint = ord(character)
        if codepoint <= 0x1F or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            rendered.append(f"\\x{codepoint:02x}")
        else:
            rendered.append(character)
    return "".join(rendered)
