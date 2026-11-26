from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from offerradar_agents.matcher.eval import split_holdout


@dataclass(frozen=True)
class ScoredRun:
    prompt_version: str
    provider: str
    model: str
    scores: dict[int, int | None]


@dataclass(frozen=True)
class ScoreEvalReport:
    prompt_version: str
    provider: str
    model: str
    holdout: bool
    rated: int
    failed: int
    spearman: float
    mae: float


def load_ratings(path: Path) -> dict[int, int]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    ratings: dict[int, int] = {}
    for entry in raw["postings"]:
        posting_id, rating = entry["posting_id"], entry["rating"]
        if posting_id in ratings:
            raise ValueError(f"{path}: duplicate posting_id {posting_id}")
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError(f"{path}: posting {posting_id} rating {rating!r} is outside 1..5")
        ratings[posting_id] = rating
    return ratings


# The hash pins the exact text that was rated, so a re-poll or re-seed cannot swap the posting.
def load_rated_hashes(path: Path) -> dict[int, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {entry["posting_id"]: entry["text_hash"] for entry in raw["postings"]}


def load_scored(path: Path) -> ScoredRun:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ScoredRun(
        prompt_version=raw["prompt_version"],
        provider=raw["provider"],
        model=raw["model"],
        scores={int(k): v for k, v in raw["scores"].items()},
    )


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


# NaN when fewer than 2 points or either side is constant.
def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) != len(ys):
        raise ValueError("xs and ys differ in length")
    if len(xs) < 2:
        return math.nan
    rx, ry = _average_ranks(xs), _average_ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    var_x = sum((a - mx) ** 2 for a in rx)
    var_y = sum((b - my) ** 2 for b in ry)
    if var_x == 0 or var_y == 0:
        return math.nan
    return cov / math.sqrt(var_x * var_y)


def evaluate(
    ratings: dict[int, int],
    run: ScoredRun,
    *,
    holdout: bool,
    fraction: float = 0.3,
    seed: int = 0,
) -> ScoreEvalReport:
    tuning, held = split_holdout(list(ratings), fraction=fraction, seed=seed)
    ids = held if holdout else tuning
    pairs = [(ratings[i], score) for i in ids if (score := run.scores.get(i)) is not None]
    return ScoreEvalReport(
        prompt_version=run.prompt_version,
        provider=run.provider,
        model=run.model,
        holdout=holdout,
        rated=len(ids),
        failed=len(ids) - len(pairs),
        spearman=spearman([r for r, _ in pairs], [s for _, s in pairs]),
        mae=(sum(abs((r - 1) * 25 - s) for r, s in pairs) / len(pairs) if pairs else math.nan),
    )


def format_report(report: ScoreEvalReport) -> str:
    return "\n".join(
        [
            f"matcher score eval ({'holdout' if report.holdout else 'tuning'})"
            f" prompt={report.prompt_version} provider={report.provider} model={report.model}",
            f"  rated      {report.rated}",
            f"  failed     {report.failed}",
            f"  spearman   {report.spearman:.3f}",
            f"  mae        {report.mae:.1f}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offerradar_agents.matcher.score_eval")
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        ratings = load_ratings(args.ratings)
        run = load_scored(args.run)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot load eval input: {exc!r}", file=sys.stderr)
        return 1
    for holdout in (False, True):
        print(format_report(evaluate(ratings, run, holdout=holdout)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
