from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from offerradar_agents.matcher.rank import score_posting

RELEVANT_RATING = 4.0


@dataclass(frozen=True)
class RatedPosting:
    posting_id: int
    vector: tuple[float, ...]
    rating: float
    holdout: bool = False


@dataclass(frozen=True)
class RankingFixture:
    embedding_model: str
    chunk_vectors: tuple[tuple[float, ...], ...]
    postings: tuple[RatedPosting, ...]
    placeholder: bool = False


@dataclass(frozen=True)
class RecordedChunk:
    ordinal: int
    vector: tuple[float, ...]


@dataclass(frozen=True)
class RetrievalQuery:
    query_id: str
    text: str
    vector: tuple[float, ...]
    relevant_ordinals: frozenset[int]


@dataclass(frozen=True)
class RetrievalFixture:
    embedding_model: str
    chunks: tuple[RecordedChunk, ...]
    queries: tuple[RetrievalQuery, ...]
    placeholder: bool = False


@dataclass(frozen=True)
class RankingReport:
    rated: int
    holdout: bool
    k: int
    recall_at_k: float
    spearman: float
    placeholder: bool = False


@dataclass(frozen=True)
class RetrievalReport:
    queries: int
    k: int
    recall_at_k: float
    placeholder: bool = False


def _vector(raw: Sequence[float]) -> tuple[float, ...]:
    return tuple(float(x) for x in raw)


def _same_dimension(vectors: Sequence[tuple[float, ...]], source: Path) -> None:
    if len({len(v) for v in vectors}) > 1:
        raise ValueError(f"{source}: vectors differ in dimension")


def load_ranking_fixture(path: Path) -> RankingFixture:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks = tuple(_vector(v) for v in raw["chunks"])
    rated = tuple(
        RatedPosting(
            posting_id=entry["id"],
            vector=_vector(entry["vector"]),
            rating=float(entry["rating"]),
            holdout=entry.get("holdout", False),
        )
        for entry in raw["postings"]
    )
    _same_dimension([*chunks, *(p.vector for p in rated)], path)
    return RankingFixture(
        embedding_model=raw["embedding_model"],
        chunk_vectors=chunks,
        postings=rated,
        placeholder=raw.get("placeholder", False),
    )


def load_retrieval_fixture(path: Path) -> RetrievalFixture:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks = tuple(
        RecordedChunk(ordinal=entry["ordinal"], vector=_vector(entry["vector"]))
        for entry in raw["chunks"]
    )
    queries = tuple(
        RetrievalQuery(
            query_id=entry["id"],
            text=entry["text"],
            vector=_vector(entry["vector"]),
            relevant_ordinals=frozenset(entry["relevant"]),
        )
        for entry in raw["queries"]
    )
    _same_dimension([*(c.vector for c in chunks), *(q.vector for q in queries)], path)
    known = {c.ordinal for c in chunks}
    for query in queries:
        if not query.relevant_ordinals <= known:
            raise ValueError(f"{path}: query {query.query_id} names an unknown chunk ordinal")
    return RetrievalFixture(
        embedding_model=raw["embedding_model"],
        chunks=chunks,
        queries=queries,
        placeholder=raw.get("placeholder", False),
    )


def _hash_unit(seed: int, item: int) -> float:
    return int.from_bytes(hashlib.sha256(f"{seed}:{item}".encode()).digest()[:8], "big") / 2**64


def _shuffled(ids: Sequence[int], seed: int) -> list[int]:
    return sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).digest())


# Ranks are 1-based. Returned in shuffled order so the rater cannot read rank off position.
def sample_for_rating(
    ranked_ids: Sequence[int], *, lo: int = 21, hi: int = 200, n: int, seed: int
) -> list[int]:
    if lo < 1 or hi < lo:
        raise ValueError(f"bad rank range {lo}..{hi}")
    pool = ranked_ids[lo - 1 : hi]
    if n > len(pool):
        raise ValueError(f"asked for {n} postings from a pool of {len(pool)}")
    return _shuffled(pool, seed)[:n]


# Per-id membership: rating more postings later never moves a tuning id into the holdout.
def split_holdout(ids: Sequence[int], *, fraction: float, seed: int) -> tuple[list[int], list[int]]:
    if not 0 <= fraction <= 1:
        raise ValueError(f"fraction {fraction} is outside [0, 1]")
    unique = sorted(set(ids))
    holdout = [i for i in unique if _hash_unit(seed, i) < fraction]
    held = set(holdout)
    return [i for i in unique if i not in held], holdout


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0:
        raise ValueError("a fixture vector has zero norm")
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for i in range(start, end + 1):
            ranks[order[i]] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    var = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return cov / var if var else math.nan


# recall_at_k and spearman are NaN when undefined; ties break as in rank_postings.
def evaluate_ranking(
    fixture: RankingFixture, *, holdout: bool = False, k: int = 20
) -> RankingReport:
    rows = [p for p in fixture.postings if p.holdout == holdout]
    scores = [
        score_posting([_cosine(p.vector, chunk) for chunk in fixture.chunk_vectors]) for p in rows
    ]
    order = sorted(range(len(rows)), key=lambda i: (-scores[i], rows[i].posting_id))
    top = {rows[i].posting_id for i in order[:k]}
    relevant = [p.posting_id for p in rows if p.rating >= RELEVANT_RATING]
    recall = sum(i in top for i in relevant) / len(relevant) if relevant else math.nan
    spearman = _spearman([p.rating for p in rows], scores) if len(rows) > 1 else math.nan
    return RankingReport(
        rated=len(rows),
        holdout=holdout,
        k=k,
        recall_at_k=recall,
        spearman=spearman,
        placeholder=fixture.placeholder,
    )


def evaluate_retrieval(fixture: RetrievalFixture, *, k: int = 5) -> RetrievalReport:
    recalls = []
    for query in fixture.queries:
        if not query.relevant_ordinals:
            raise ValueError("a retrieval query has no relevant chunks")
        nearest = sorted(
            fixture.chunks, key=lambda c: (-_cosine(query.vector, c.vector), c.ordinal)
        )[:k]
        hits = query.relevant_ordinals & {c.ordinal for c in nearest}
        recalls.append(len(hits) / len(query.relevant_ordinals))
    return RetrievalReport(
        queries=len(recalls),
        k=k,
        recall_at_k=sum(recalls) / len(recalls) if recalls else math.nan,
        placeholder=fixture.placeholder,
    )


def format_report(report: RankingReport | RetrievalReport) -> str:
    if isinstance(report, RankingReport):
        lines = [
            f"matcher ranking eval ({'holdout' if report.holdout else 'tuning'})",
            f"  rated          {report.rated}",
            f"  recall@{report.k:<8}{report.recall_at_k:.3f}",
            f"  spearman       {report.spearman:.3f}",
        ]
    else:
        lines = [
            "matcher retrieval eval",
            f"  queries        {report.queries}",
            f"  recall@{report.k:<8}{report.recall_at_k:.3f}",
        ]
    if report.placeholder:
        lines.append("  PLACEHOLDER    fixture is not real ratings")
    return "\n".join(lines)
