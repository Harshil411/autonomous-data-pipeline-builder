"""Showing a ready-made request as a goal line and bullets.

A request such as "Load the CSVs and join them. Amounts arrive as text and must become
numbers; dates come in several formats ..." is one goal followed by a list of rules. On
screen it reads better as

    Load the CSVs and join them.

    • Amounts arrive as text and must become numbers
    • Dates come in several formats ...

This is a way of *showing* the text, not a rewrite. The request is the instruction the
agents receive and every benchmark result was measured on it, so:

    as_bullets(text)             the same words in the same order, laid out as bullets
    text_to_send(shown, original)  the original text if the visitor did not edit the box,
                                   otherwise exactly what they typed
"""

from __future__ import annotations

import re

BULLET = "• "
_STARTS_A_CLAUSE = re.compile(r'[A-Za-z"(]')


def _clauses(text: str) -> list[str]:
    """Split at a full stop or semicolon that is followed by a space and the start of a new
    clause - but never inside brackets or quotes, so '(tier lower-cased; missing tier stays
    empty)' stays whole. '$1,234.50' and '21.5 C' do not split: a digit follows their dot."""
    parts, start, depth, quoted = [], 0, 0, False
    for i, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif (ch in ".;" and depth == 0 and not quoted and text[i + 1:i + 2] == " "
              and _STARTS_A_CLAUSE.match(text[i + 2:i + 3] or "")):
            parts.append(text[start:i + 1])
            start = i + 2
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def as_bullets(text: str) -> str:
    """Lay a request out as its first sentence plus one bullet per following rule.
    Text with a single sentence is returned unchanged."""
    parts = _clauses(" ".join(text.split()))
    if len(parts) < 2:
        return text.strip()
    goal, rules = parts[0], parts[1:]
    if goal.endswith(";"):
        goal = goal[:-1] + "."
    bullets = [BULLET + rule[0].upper() + rule[1:].rstrip(".;") for rule in rules]
    return "\n".join([goal, "", *bullets])


def text_to_send(shown: str, original: str | None) -> str:
    """What the agents receive. If the box still holds the bulleted form of the example,
    that is the example's original text; any edit is sent exactly as typed."""
    if original is not None and shown.strip() == as_bullets(original):
        return original
    return shown
