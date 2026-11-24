from __future__ import annotations

from offerradar_agents.matcher.postings import posting_embedding_text


def test_joins_all_parts() -> None:
    text = posting_embedding_text(
        title="SWE Intern", company="Acme", location="Remote", term="Summer 2027"
    )
    assert text == "SWE Intern | Acme | Remote | Summer 2027"


def test_skips_missing_parts() -> None:
    text = posting_embedding_text(title="SWE Intern", company="Acme", location=None, term=None)
    assert text == "SWE Intern | Acme"


def test_skips_empty_parts() -> None:
    text = posting_embedding_text(title="SWE Intern", company="Acme", location="", term="Fall")
    assert text == "SWE Intern | Acme | Fall"
