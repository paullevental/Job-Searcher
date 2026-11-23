from __future__ import annotations

import json
from pathlib import Path

import pytest

from offerradar_agents.scout.readme_table import ReadmeRow, parse_readme_table

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def rows() -> list[ReadmeRow]:
    markdown = json.loads((FIXTURES / "simplify_readme.json").read_text())["markdown"]
    return parse_readme_table(markdown)


def test_a_pipe_table_yields_nothing(rows: list[ReadmeRow]) -> None:
    # The lists moved to HTML tables; the markdown parser this replaced found
    # zero rows against the real README and collected nothing in production.
    assert parse_readme_table("| Company | Role |\n|---|---|\n| Acme | SWE Intern |\n") == []
    assert rows


def test_every_row_is_parsed(rows: list[ReadmeRow]) -> None:
    assert len(rows) == 7


def test_a_continuation_row_inherits_the_company_above_it(rows: list[ReadmeRow]) -> None:
    assert [row.company for row in rows[1:4]] == ["DataWorks"] * 3
    assert rows[2].role == "Machine Learning Intern"


def test_the_employer_link_wins_over_the_aggregator(rows: list[ReadmeRow]) -> None:
    assert rows[0].application_url.startswith("https://job-boards.greenhouse.io/examplecorp")


def test_an_aggregator_only_row_is_locked(rows: list[ReadmeRow]) -> None:
    taken_down = rows[4]
    assert taken_down.company == "SecureStack"
    assert taken_down.locked


def test_a_lock_emoji_marks_the_row_and_leaves_the_title(rows: list[ReadmeRow]) -> None:
    marked = rows[5]
    assert marked.locked
    assert marked.role == "Platform Intern"


def test_two_locations_are_split_not_concatenated(rows: list[ReadmeRow]) -> None:
    assert rows[0].location == "Remote in USA / Austin, TX"


def test_a_collapsed_location_list_drops_its_summary_count(rows: list[ReadmeRow]) -> None:
    assert rows[6].location == "Madison, WI / Durham, NC / Broomfield, CO"
