from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from offerradar_agents.config import get_settings
from offerradar_agents.db.session import build_engine, build_session_factory
from offerradar_agents.embeddings.errors import EMBEDDING_ERRORS
from offerradar_agents.embeddings.ollama import OllamaEmbeddingProvider
from offerradar_agents.matcher.store import store_resume


async def _embed_resume(markdown: str) -> int:
    settings = get_settings()
    engine = build_engine(settings)
    try:
        async with (
            OllamaEmbeddingProvider(settings=settings) as provider,
            build_session_factory(engine)() as session,
            session.begin(),
        ):
            result = await store_resume(
                session, provider, markdown=markdown, model=settings.embedding_model
            )
    except EMBEDDING_ERRORS as exc:
        print(f"embedding failed: {exc!r}. Is Ollama running?", file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print(f"not implemented: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"invalid chunks: {exc}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError) as exc:
        print(f"database unreachable: {exc!r}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    if result.skipped:
        print(f"already embedded: version {result.version}, {result.chunk_count} chunks")
    else:
        print(f"embedded: version {result.version}, {result.chunk_count} chunks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="offerradar_agents.matcher")
    commands = parser.add_subparsers(dest="command", required=True)
    embed = commands.add_parser("embed-resume")
    embed.add_argument("--resume", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        markdown = args.resume.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read resume: {exc}", file=sys.stderr)
        return 1
    return asyncio.run(_embed_resume(markdown))


if __name__ == "__main__":
    raise SystemExit(main())
