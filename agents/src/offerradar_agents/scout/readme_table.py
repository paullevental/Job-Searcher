from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

# The lists render as HTML tables inside the markdown, not pipe tables.
CELLS_PER_ROW: Final = 5
# A company cell holding only this means "same company as the row above".
CONTINUATION: Final = "↳"
CLOSED_MARK: Final = "\U0001f512"
# Decorates a company cell to flag a well-known employer; not part of the name.
HOT_MARK: Final = "\U0001f525"
# Every apply cell also links to simplify.jobs; the employer link is the other one.
AGGREGATOR_HOSTS: Final = frozenset({"simplify.jobs", "www.simplify.jobs"})
# A <br> inside a cell separates two locations for one posting.
LOCATION_SEPARATOR: Final = " / "


@dataclass(frozen=True)
class ReadmeRow:
    company: str
    role: str
    location: str | None
    application_url: str
    date_posted: str | None
    locked: bool


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.hrefs: list[list[list[str]]] = []
        self._in_body = False
        self._in_summary = False
        self._cells: list[str] | None = None
        self._cell_hrefs: list[list[str]] | None = None
        self._text: list[str] | None = None
        self._links: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tbody":
            self._in_body = True
        elif tag == "summary" and self._text is not None:
            # <summary> holds a "5 locations" count, not a location.
            self._in_summary = True
        elif tag == "tr" and self._in_body:
            self._cells, self._cell_hrefs = [], []
        elif tag == "td" and self._cells is not None:
            self._text, self._links = [], []
        elif tag == "br" and self._text is not None:
            self._text.append(LOCATION_SEPARATOR)
        elif tag == "a" and self._links is not None:
            href = dict(attrs).get("href")
            if href:
                self._links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tbody":
            self._in_body = False
        elif tag == "summary":
            self._in_summary = False
        elif tag == "td" and self._cells is not None and self._text is not None:
            assert self._cell_hrefs is not None and self._links is not None
            self._cells.append("".join(self._text).strip())
            self._cell_hrefs.append(self._links)
            self._text, self._links = None, None
        elif tag == "tr" and self._cells is not None:
            assert self._cell_hrefs is not None
            if len(self._cells) == CELLS_PER_ROW:
                self.rows.append(self._cells)
                self.hrefs.append(self._cell_hrefs)
            self._cells, self._cell_hrefs = None, None

    def handle_data(self, data: str) -> None:
        if self._text is not None and not self._in_summary:
            self._text.append(data)


# Returns the employer link and whether one was found; a row that only links to
# the aggregator has had its original listing taken down.
def _employer_url(links: list[str]) -> tuple[str, bool]:
    for href in links:
        host = href.split("//", 1)[-1].split("/", 1)[0].casefold()
        if host not in AGGREGATOR_HOSTS:
            return href, True
    return (links[0] if links else ""), False


def parse_readme_table(markdown: str) -> list[ReadmeRow]:
    parser = _TableParser()
    parser.feed(markdown)
    rows: list[ReadmeRow] = []
    last_company = ""
    for cells, hrefs in zip(parser.rows, parser.hrefs, strict=True):
        company_cell, role_cell, location, _, date = cells
        company_cell = company_cell.replace(HOT_MARK, "").strip()
        company = last_company if company_cell == CONTINUATION else company_cell
        url, is_live = _employer_url(hrefs[3])
        if not company or not role_cell or not url:
            continue
        last_company = company
        rows.append(
            ReadmeRow(
                company=company,
                role=role_cell.replace(CLOSED_MARK, "").strip(),
                location=location or None,
                application_url=url,
                date_posted=date or None,
                locked=CLOSED_MARK in role_cell or not is_live,
            )
        )
    return rows
