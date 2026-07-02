"""Read/write markdown documents with a YAML frontmatter block."""
from __future__ import annotations

import re

import yaml

_DELIM = "---"
# The closing fence is `---` alone on its own line. Matching a bare "---" substring
# would break on frontmatter values that legitimately contain it — e.g. a stored
# chat message with a markdown table separator (`|---|---|`) or a `---` rule.
# safe_dump always indents multi-line scalar content, so `---` never lands at
# column 0 inside a value; anchoring to line start is safe.
_CLOSE = re.compile(r"^---[ \t]*$", re.MULTILINE)


def parse(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body)."""
    if text.startswith(_DELIM + "\n") or text.rstrip() == _DELIM:
        after = text[len(_DELIM):].lstrip("\n")
        m = _CLOSE.search(after)
        if m:
            frontmatter = yaml.safe_load(after[:m.start()]) or {}
            return frontmatter, after[m.end():].lstrip("\n")
    return {}, text


def serialize(frontmatter: dict, body: str) -> str:
    """Emit a markdown doc with a YAML frontmatter block."""
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIM}\n{fm}\n{_DELIM}\n\n{body.rstrip()}\n"
