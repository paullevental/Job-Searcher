from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

ChunkKind = Literal["bullet", "project", "course"]


@dataclass(frozen=True)
class ResumeChunk:
    kind: ChunkKind
    ordinal: int
    text: str


# Content-addressed (plan D3): any edit to resume.md forces a re-embed.
def resume_version(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:12]


# A bold label such as **Tech:** introduces prose, not a project.
def chunk_resume(markdown: str) -> list[ResumeChunk]:
    chunks: list[ResumeChunk] = []
    kind: ChunkKind | None = None
    lines: list[str] = []
    in_courses = False
    paragraph_start = True
    seen: set[str] = set()

    def flush() -> None:
        nonlocal kind, lines
        text = " ".join(lines).removeprefix("†").strip()
        if kind is not None and text and text not in seen:
            seen.add(text)
            chunks.append(ResumeChunk(kind, len(chunks), text))
        kind, lines = None, []

    for raw in markdown.splitlines():
        line = raw.strip()
        starts_paragraph, paragraph_start = paragraph_start, not line or line.startswith("#")
        if not line or line.startswith(("#", "---", "|")):
            flush()
            if line.startswith("#"):
                in_courses = "course" in line.lower()
        elif line.startswith(("- ", "* ")):
            flush()
            kind = "course" if in_courses else "bullet"
            lines = [line[2:]]
        elif kind is not None:
            lines.append(line)
        elif starts_paragraph and re.match(r"\*\*[^*]*[^*:]\*\*", line):
            kind, lines = "project", [line]
    flush()
    return chunks
