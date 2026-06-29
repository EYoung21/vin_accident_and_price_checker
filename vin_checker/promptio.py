"""Shared paste-aware terminal input (Claude-Code-style bracketed paste).

Used by both the main prompt and the follow-up chat so a multi-line paste is ONE
message (collapsed to a placeholder), not one message per line.
"""

from __future__ import annotations

import sys


def _basic(ps: str, show_hint: bool) -> str:
    if show_hint:
        print("(paste it all, then press Enter on an empty line to finish)")
    print(ps, end="", flush=True)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "":
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def read_block(banner: str | None = None, ps: str = "> ", show_hint: bool = True) -> str:
    """Read possibly-multi-line input. A pasted block collapses to a placeholder and
    is expanded back before returning, so it counts as a single message."""
    if banner:
        print(banner)
    if not sys.stdin.isatty():
        return _basic(ps, show_hint)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.keys import Keys
    except ImportError:
        return _basic(ps, show_hint)

    if show_hint:
        print("(paste freely — big pastes collapse to a placeholder; Enter sends, "
              "Option+Enter / Shift+Enter = new line)")
    kb = KeyBindings()
    pastes: dict[str, str] = {}

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("c-j")
    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    @kb.add(Keys.BracketedPaste)
    def _(event):
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in data and len(data) <= 80:
            event.current_buffer.insert_text(data)
            return
        token = f"[Pasted text #{len(pastes) + 1} +{data.count(chr(10)) + 1} lines]"
        pastes[token] = data
        event.current_buffer.insert_text(token)

    try:
        text = PromptSession(multiline=True, key_bindings=kb).prompt(ps)
    except (EOFError, KeyboardInterrupt):
        return ""
    for token, data in pastes.items():
        text = text.replace(token, data)
    return text.strip()
