"""Read/write markdown documents with a YAML frontmatter block."""
from __future__ import annotations

import re

import yaml

# Parsing frontmatter is the platform's hottest operation: every loop beat re-reads
# and re-parses the substrate, and pure-python YAML is ~12x slower than libyaml on
# our documents. Use the C loader when the wheel ships it, and fall back to the
# pure-python one both when it's absent and if it ever rejects a document the
# python loader accepts — so this can only ever be faster, never stricter.
_CLoader = getattr(yaml, "CSafeLoader", None)

_DELIM = "---"
# The closing fence is `---` alone on its own line. Matching a bare "---" substring
# would break on frontmatter values that legitimately contain it — e.g. a stored
# chat message with a markdown table separator (`|---|---|`) or a `---` rule.
# safe_dump always indents multi-line scalar content, so `---` never lands at
# column 0 inside a value; anchoring to line start is safe.
_CLOSE = re.compile(r"^---[ \t]*$", re.MULTILINE)


def _load(block: str) -> dict:
    if _CLoader is not None:
        try:
            return yaml.load(block, Loader=_CLoader) or {}
        except yaml.YAMLError:
            pass          # let the reference loader decide: parse it, or raise as before
    return yaml.safe_load(block) or {}


def parse(text: str) -> tuple[dict, str]:
    """Split a markdown doc into (frontmatter dict, body)."""
    if text.startswith(_DELIM + "\n") or text.rstrip() == _DELIM:
        after = text[len(_DELIM):].lstrip("\n")
        m = _CLOSE.search(after)
        if m:
            frontmatter = _load(after[:m.start()])
            return frontmatter, after[m.end():].lstrip("\n")
    return {}, text


def serialize(frontmatter: dict, body: str) -> str:
    """Emit a markdown doc with a YAML frontmatter block."""
    fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"{_DELIM}\n{fm}\n{_DELIM}\n\n{body.rstrip()}\n"
