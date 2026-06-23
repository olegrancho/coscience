"""Read/write markdown documents with a YAML frontmatter block."""
from __future__ import annotations

import yaml

_DELIM = "---"


def parse(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body)."""
    if text.startswith(_DELIM):
        parts = text.split(_DELIM, 2)
        # parts == ['', '\n<yaml>\n', '\n<body>'] for a well-formed doc
        if len(parts) == 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2]
            if body.startswith("\n"):
                body = body[1:]
            return frontmatter, body.lstrip("\n")
    return {}, text


def serialize(frontmatter: dict, body: str) -> str:
    """Emit a markdown doc with a YAML frontmatter block."""
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIM}\n{fm}\n{_DELIM}\n\n{body.rstrip()}\n"
