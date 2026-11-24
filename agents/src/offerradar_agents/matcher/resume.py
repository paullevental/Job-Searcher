from __future__ import annotations

import hashlib
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


# Paul writes this (AGENTS.md §11 M4).
def chunk_resume(markdown: str) -> list[ResumeChunk]:
    raise NotImplementedError("chunk_resume is Paul's (AGENTS.md §11 M4)")
