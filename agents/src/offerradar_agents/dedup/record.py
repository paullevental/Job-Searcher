from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.dedup.eval import (
    derive_thresholds,
    evaluate,
    format_report,
    load_pairs,
)
from offerradar_agents.embeddings.errors import EMBEDDING_ERRORS
from offerradar_agents.embeddings.ollama import OllamaEmbeddingProvider
from offerradar_agents.embeddings.provider import DEFAULT_DOCUMENT_PREFIX, Vector

DEFAULT_PAIRS = Path("tests/fixtures/dedup_pairs.json")


async def embed_titles(titles: list[str], settings: Settings) -> dict[str, Vector]:
    async with OllamaEmbeddingProvider(settings=settings) as provider:
        return dict(zip(titles, await provider.embed(titles), strict=True))


# 6 decimals keeps the fixture under the 500 KB pre-commit cap; cosine moves by ~1e-5.
def write_vectors(out: Path, vectors: dict[str, Vector], settings: Settings) -> None:
    payload = {
        "model": settings.embedding_model,
        "prefix": DEFAULT_DOCUMENT_PREFIX,
        "recorded_at": datetime.now(UTC).isoformat(),
        "vectors": {title: [round(v, 6) for v in vector] for title, vector in vectors.items()},
    }
    out.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")


def record(*, pairs_path: Path, out: Path, settings: Settings) -> None:
    pairs = load_pairs(pairs_path)
    titles = sorted({title for pair in pairs for title in (pair.title_a, pair.title_b)})
    vectors = asyncio.run(embed_titles(titles, settings))
    write_vectors(out, vectors, settings)

    thresholds = derive_thresholds(pairs, vectors)
    print(f"recorded {len(vectors)} titles with {settings.embedding_model} to {out}")
    print(f"derived DEDUP_MERGE_THRESHOLD={thresholds.merge!r}")
    print(f"derived DEDUP_REVIEW_THRESHOLD={thresholds.review!r}")
    print("at the configured thresholds:")
    print(format_report(evaluate(pairs, vectors)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offerradar_agents.dedup.record")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS)
    args = parser.parse_args(argv)
    try:
        record(pairs_path=args.pairs, out=args.out, settings=get_settings())
    except EMBEDDING_ERRORS as exc:
        print(f"embedding failed: {exc!r}. Is Ollama running?", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"vectors recorded, but no threshold separates the labels: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
