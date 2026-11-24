from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from offerradar_agents.dedup.fuzzy import fuzzy_match, similarity_score, unmatched_tokens
from offerradar_agents.dedup.staleness import ATS_SOURCES
from offerradar_agents.embeddings.provider import Vector


@dataclass(frozen=True)
class LabeledPair:
    pair_id: str
    title_a: str
    title_b: str
    duplicate: bool
    # Where each side was seen, "a|b". open_siblings refuses a pair sharing an ATS
    # board, so the eval must refuse it too or it scores a path that cannot run.
    sources: tuple[str, str] = ("", "")
    placeholder: bool = False

    @property
    def shares_an_ats_board(self) -> bool:
        a, b = self.sources
        return a == b and a in ATS_SOURCES


@dataclass(frozen=True)
class RecordedVectors:
    model: str
    prefix: str
    vectors: Mapping[str, Vector]


@dataclass(frozen=True)
class Thresholds:
    merge: float
    review: float


@dataclass(frozen=True)
class EvalReport:
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    review_band: int
    same_board: int
    placeholders: int
    # Pairs with a title missing from the recorded vectors: left out of every count above.
    unscored: int = 0

    @property
    def total(self) -> int:
        return (
            self.true_positives + self.false_positives + self.false_negatives + self.true_negatives
        )

    # 1.0 on an empty denominator: nothing was predicted positive, so nothing was
    # predicted wrongly. Reported alongside `total`, which shows the sample size.
    @property
    def precision(self) -> float:
        predicted = self.true_positives + self.false_positives
        return 1.0 if predicted == 0 else self.true_positives / predicted

    @property
    def recall(self) -> float:
        actual = self.true_positives + self.false_negatives
        return 1.0 if actual == 0 else self.true_positives / actual


def _sources(raw: str) -> tuple[str, str]:
    parts = raw.split("|")
    return (parts[0], parts[1]) if len(parts) == 2 else ("", "")


def load_pairs(path: Path) -> tuple[LabeledPair, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        LabeledPair(
            pair_id=entry["id"],
            title_a=entry["title_a"],
            title_b=entry["title_b"],
            duplicate=entry["duplicate"],
            sources=_sources(entry.get("sources", "")),
            placeholder=entry.get("placeholder", False),
        )
        for entry in raw["pairs"]
    )


def load_vectors(path: Path) -> RecordedVectors:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return RecordedVectors(model=raw["model"], prefix=raw["prefix"], vectors=raw["vectors"])


def _score(pair: LabeledPair, vectors: Mapping[str, Vector]) -> float | None:
    if pair.title_a not in vectors or pair.title_b not in vectors:
        return None
    return similarity_score(vectors[pair.title_a], vectors[pair.title_b])


# "merge" is the positive prediction, not "review": only merge acts without Paul.
def evaluate(
    pairs: tuple[LabeledPair, ...], vectors: Mapping[str, Vector] | None = None
) -> EvalReport:
    known = vectors if vectors is not None else {}
    tp = fp = fn = tn = review = same_board = unscored = 0
    for pair in pairs:
        if pair.shares_an_ats_board:
            same_board += 1
            verdict = "distinct"
        else:
            score = _score(pair, known)
            if score is None:
                unscored += 1
                continue
            verdict = fuzzy_match(pair.title_a, pair.title_b, score=score)
        if verdict == "review":
            review += 1
        predicted = verdict == "merge"
        if predicted and pair.duplicate:
            tp += 1
        elif predicted:
            fp += 1
        elif pair.duplicate:
            fn += 1
        else:
            tn += 1
    return EvalReport(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        review_band=review,
        same_board=same_board,
        placeholders=sum(1 for pair in pairs if pair.placeholder),
        unscored=unscored,
    )


# Merge: lowest clean duplicate above every clean non-duplicate. Review: lowest duplicate.
def derive_thresholds(pairs: tuple[LabeledPair, ...], vectors: Mapping[str, Vector]) -> Thresholds:
    scored = [
        (pair, score)
        for pair in pairs
        if not pair.shares_an_ats_board and (score := _score(pair, vectors)) is not None
    ]
    mergeable = [(p, s) for p, s in scored if not unmatched_tokens(p.title_a, p.title_b)]
    worst_false = max((s for p, s in mergeable if not p.duplicate), default=-1.0)
    merge = min((s for p, s in mergeable if p.duplicate and s > worst_false), default=None)
    if merge is None:
        raise ValueError("no cutoff merges a labelled duplicate at precision 1.0")
    review = min((s for p, s in scored if p.duplicate), default=merge)
    return Thresholds(merge=merge, review=min(review, merge))


def format_report(report: EvalReport) -> str:
    tp = report.true_positives
    lines = [
        "dedup fuzzy-match eval",
        f"  pairs          {report.total}",
        f"  precision      {report.precision:.3f}  (tp={tp} fp={report.false_positives})",
        f"  recall         {report.recall:.3f}  (tp={tp} fn={report.false_negatives})",
        f"  review band    {report.review_band}",
        f"  same board     {report.same_board}  (refused before scoring)",
    ]
    if report.unscored:
        lines.append(
            f"  UNSCORED       {report.unscored} pairs have no recorded vector"
            " (make record-dedup-vectors)"
        )
    if report.placeholders:
        lines.append(
            f"  PLACEHOLDERS   {report.placeholders} of {report.total} pairs are not real labels"
        )
    return "\n".join(lines)
