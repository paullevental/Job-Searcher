from __future__ import annotations

from pathlib import Path

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


def test_version_is_twelve_hex_chars() -> None:
    version = resume_version("# resume")
    assert len(version) == 12
    int(version, 16)


def test_version_is_deterministic() -> None:
    assert resume_version("same") == resume_version("same")


def test_version_changes_with_any_edit() -> None:
    assert resume_version("a bullet") != resume_version("a bullet.")


def test_chunks_are_resume_chunks() -> None:
    chunks = chunk_resume(RESUME)
    assert chunks
    assert all(isinstance(c, ResumeChunk) for c in chunks)


def test_chunk_kinds_are_valid() -> None:
    chunks = chunk_resume(RESUME)
    assert {c.kind for c in chunks} <= KINDS


def test_chunk_ordinals_are_contiguous_from_zero() -> None:
    chunks = chunk_resume(RESUME)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_chunk_text_is_non_empty() -> None:
    chunks = chunk_resume(RESUME)
    assert all(c.text.strip() for c in chunks)


def test_chunking_is_deterministic() -> None:
    assert chunk_resume(RESUME) == chunk_resume(RESUME)


def test_bullet_kind() -> None:
    chunks = chunk_resume("- Cut runtime 40%.")
    assert chunks == [ResumeChunk("bullet", 0, "Cut runtime 40%.")]


def test_project_kind() -> None:
    chunks = chunk_resume("**Tracker** - Python, FastAPI")
    assert chunks == [ResumeChunk("project", 0, "**Tracker** - Python, FastAPI")]


def test_course_kind_under_course_heading() -> None:
    chunks = chunk_resume("## Relevant Coursework\n\n- Databases\n\n## Skills\n\n- Python")
    assert [c.kind for c in chunks] == ["course", "bullet"]


def test_dagger_is_stripped() -> None:
    assert chunk_resume("- † Designed a ranker.")[0].text == "Designed a ranker."


def test_multiline_list_item_is_joined() -> None:
    chunks = chunk_resume("- First line\n  second line\n- Next")
    assert [c.text for c in chunks] == ["First line second line", "Next"]


def test_prose_is_ignored() -> None:
    chunks = chunk_resume("# Title\n\nIntro prose.\n\n---\n\nMore prose.\n\n- Item")
    assert [c.text for c in chunks] == ["Item"]


def test_real_resume_yields_chunks() -> None:
    markdown = (Path(__file__).resolve().parents[2] / "resume.md").read_text()
    chunks = chunk_resume(markdown)
    assert len(chunks) > 5
    assert all(c.text for c in chunks)


def test_duplicate_items_become_one_chunk() -> None:
    chunks = chunk_resume("## A\n\n- Built X.\n\n## B\n\n- Built X.\n- Built Y.\n")
    assert [c.text for c in chunks] == ["Built X.", "Built Y."]
    assert [c.ordinal for c in chunks] == [0, 1]


def test_bold_label_and_mid_paragraph_bold_are_not_projects() -> None:
    markdown = "Intro line\n**Designed** flips later.\n\n**Tech:** Python\n\n**Radar** — a tool.\n"
    assert [(c.kind, c.text) for c in chunk_resume(markdown)] == [
        ("project", "**Radar** — a tool.")
    ]


def test_project_directly_under_a_heading_is_kept() -> None:
    chunks = chunk_resume("## Projects\n**Radar** — a tool.\n")
    assert [(c.kind, c.text) for c in chunks] == [("project", "**Radar** — a tool.")]
