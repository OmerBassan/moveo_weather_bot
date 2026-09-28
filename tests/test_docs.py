"""The design document must quote the system prompt it claims to quote.

The prompt is one of the deliverables the brief asks for by name, and
`architecture.md` §6 says "verbatim". Nothing kept that honest: the document
drifted four rules behind `prompt.py` and no test noticed, because a stale
quotation breaks no code path -- it only misleads a reader, which is the one
failure a test suite is usually blind to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.prompt import SYSTEM_PROMPT

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _quoted_prompt(document: Path) -> str:
    """The first fenced block under the system-prompt heading."""
    text = document.read_text(encoding="utf-8")
    heading = text.index("## 6. System prompt")
    opening = text.index("```", heading)
    closing = text.index("```", opening + 3)
    return text[opening + 3 : closing].strip("\n")


def test_architecture_quotes_the_prompt_verbatim() -> None:
    quoted = _quoted_prompt(DOCS / "architecture.md")
    assert quoted == SYSTEM_PROMPT.strip("\n"), (
        "docs/architecture.md \u00a76 no longer matches app/agent/prompt.py. "
        "Re-quote it from the source rather than editing the document by hand."
    )


@pytest.mark.parametrize(
    "document", ["architecture.md", "DESIGN.md", "DEPLOYMENT.md"]
)
def test_deliverable_documents_exist(document: str) -> None:
    assert (DOCS / document).is_file()


# The eight bullets the brief lists under the design document, in its order.
REQUIRED_SECTIONS = (
    "system architecture",
    "repository structure",
    "data storage",
    "scoring methodology",
    "why an llm is needed",
    "system prompt",
    "evaluation set and results",
    "key tradeoffs",
)


@pytest.mark.parametrize("required", REQUIRED_SECTIONS)
def test_design_document_has_a_section_per_required_deliverable(required: str) -> None:
    """Asserted against HEADINGS, not the whole text: every one of these phrases
    also appears in prose, so a substring search over the document would pass
    for a bullet that was never actually answered."""
    headings = [
        line.lstrip("#").strip().lower()
        for line in (DOCS / "DESIGN.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    assert any(required in heading for heading in headings), (
        f"docs/DESIGN.md has no section for {required!r}. Headings: {headings}"
    )
