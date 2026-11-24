from __future__ import annotations

import pytest

from offerradar_agents.matcher.resume import ResumeChunk, chunk_resume, resume_version

# Inline: resume.md is gitignored, so CI has no copy to read.
RESUME = """# Jane Doe

## Experience

**Acme Corp** - Software Engineering Intern

- Cut nightly batch runtime 40% by parallelising the ingest step with asyncio.
- Shipped a Postgres-backed job queue handling 2k tasks a minute.

## Projects

**Tracker** - Python, FastAPI, pgvector

- Ranked 4k postings against a resume with local embeddings.

## Coursework

Data Structures, Operating Systems, Databases
"""
KINDS = {"bullet", "project", "course"}
PENDING = pytest.mark.xfail(raises=NotImplementedError, strict=True)


def test_version_is_twelve_hex_chars() -> None:
    version = resume_version("# resume")
    assert len(version) == 12
    int(version, 16)


def test_version_is_deterministic() -> None:
    assert resume_version("same") == resume_version("same")


def test_version_changes_with_any_edit() -> None:
    assert resume_version("a bullet") != resume_version("a bullet.")


@PENDING
def test_chunks_are_resume_chunks() -> None:
    chunks = chunk_resume(RESUME)
    assert chunks
    assert all(isinstance(c, ResumeChunk) for c in chunks)


@PENDING
def test_chunk_kinds_are_valid() -> None:
    chunks = chunk_resume(RESUME)
    assert {c.kind for c in chunks} <= KINDS


@PENDING
def test_chunk_ordinals_are_contiguous_from_zero() -> None:
    chunks = chunk_resume(RESUME)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


@PENDING
def test_chunk_text_is_non_empty() -> None:
    chunks = chunk_resume(RESUME)
    assert all(c.text.strip() for c in chunks)


@PENDING
def test_chunking_is_deterministic() -> None:
    assert chunk_resume(RESUME) == chunk_resume(RESUME)
