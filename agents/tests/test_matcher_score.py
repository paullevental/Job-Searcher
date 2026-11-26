from __future__ import annotations

from types import SimpleNamespace

from offerradar_agents.matcher.score import PROMPTS_DIR, load_prompt, posting_text, text_hash


def _row(**overrides: object) -> SimpleNamespace:
    base = {
        "title": "Backend Intern",
        "company": "Acme",
        "location": "Toronto",
        "remote": False,
        "term": "Fall 2027",
        "description": None,
    }
    return SimpleNamespace(**{**base, **overrides})


def test_title_only_posting_has_no_description_block() -> None:
    text = posting_text(_row())
    assert "Description" not in text
    assert "Term: Fall 2027" in text


def test_escaped_html_description_becomes_plain_text() -> None:
    text = posting_text(_row(description="&lt;p&gt;Use &amp;lt;b&amp;gt;Python&lt;/p&gt;"))
    assert "<" not in text.split("Description:")[1]
    assert "Python" in text


def test_hash_changes_with_title() -> None:
    assert text_hash(posting_text(_row())) != text_hash(posting_text(_row(title="Data Intern")))


def test_both_prompt_versions_exist_and_name_the_tool() -> None:
    for version in ("score_v1", "score_v2"):
        assert (PROMPTS_DIR / f"{version}.md").is_file()
        assert "record_score" in load_prompt(version)
