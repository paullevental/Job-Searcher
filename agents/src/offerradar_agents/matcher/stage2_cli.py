from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from sqlalchemy.exc import SQLAlchemyError

from offerradar_agents.config import Settings, get_settings
from offerradar_agents.db.session import build_engine, build_session_factory
from offerradar_agents.llm.budget import finish_run, start_run
from offerradar_agents.llm.client import LLMClient
from offerradar_agents.llm.errors import LLMError
from offerradar_agents.llm.factory import build_provider, model_for
from offerradar_agents.llm.logs import quiet_sdk_loggers
from offerradar_agents.llm.provider import ProviderName
from offerradar_agents.matcher.gate1 import auto_forward, select_for_analysis, shortlist
from offerradar_agents.matcher.prefilter import rules_from_settings
from offerradar_agents.matcher.rank import rank_postings
from offerradar_agents.matcher.resume import resume_version
from offerradar_agents.matcher.score import (
    ScoringSetup,
    posting_text,
    ranked_rows,
    score_for_eval,
    score_selected,
    text_hash,
)
from offerradar_agents.matcher.score_eval import load_rated_hashes, load_ratings
from offerradar_agents.matcher.score_schema import SCORE_TOOL


def add_stage2_commands(commands: Any) -> None:
    rank = commands.add_parser("rank")
    rank.add_argument("--resume", type=Path, required=True)
    commands.add_parser("shortlist")
    select = commands.add_parser("select")
    select.add_argument("posting_ids", type=int, nargs="+")
    sample = commands.add_parser("sample-score-eval")
    sample.add_argument("--n", type=int, default=30)
    sample.add_argument("--out", type=Path, required=True)
    for name in ("score", "score-eval-run"):
        command = commands.add_parser(name)
        command.add_argument("--resume", type=Path, required=True)
        command.add_argument("--prompt-version")
        command.add_argument("--provider", choices=("anthropic", "ollama"))
        if name == "score-eval-run":
            command.add_argument("--ratings", type=Path, required=True)
            command.add_argument("--out", type=Path, required=True)


def _setup(settings: Settings, args: argparse.Namespace, resume_markdown: str) -> ScoringSetup:
    provider = cast(ProviderName, args.provider or settings.llm_provider)
    model = model_for(settings, provider)
    if not model:
        raise ValueError(f"no model configured for provider {provider!r}")
    return ScoringSetup(
        provider=provider,
        model=model,
        prompt_version=args.prompt_version or settings.score_prompt_version,
        resume_markdown=resume_markdown,
        max_tokens=settings.llm_max_tokens_score,
    )


async def _rank(settings: Settings, resume_markdown: str) -> int:
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session, session.begin():
            ranked = await rank_postings(
                session,
                resume_version=resume_version(resume_markdown),
                model=settings.embedding_model,
                k=settings.gate1_shortlist_size,
            )
    finally:
        await engine.dispose()
    if not ranked:
        print("nothing ranked: run make embed-resume, and check postings are embedded")
        return 1
    best = ranked[0]
    print(f"ranked every open embedded posting; best {best.posting_id} at {best.similarity:.3f}")
    return 0


async def _shortlist(settings: Settings) -> int:
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session:
            items = await shortlist(
                session, n=settings.gate1_shortlist_size, rules=rules_from_settings(settings)
            )
    finally:
        await engine.dispose()
    for item in items:
        print(f"{item.posting_id}\t{item.similarity:.3f}\t{item.company}\t{item.title}")
    return 0


async def _select(settings: Settings, posting_ids: list[int]) -> int:
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session, session.begin():
            updated = await select_for_analysis(session, posting_ids)
    finally:
        await engine.dispose()
    print(f"selected {updated} of {len(posting_ids)}")
    return 0


# Evenly spaced across the whole ranking, then shuffled so rank cannot be read off position.
async def _sample(settings: Settings, n: int) -> dict[str, Any]:
    engine = build_engine(settings)
    try:
        async with build_session_factory(engine)() as session:
            rows = await ranked_rows(session)
    finally:
        await engine.dispose()
    if n < 1 or n > len(rows):
        raise ValueError(f"asked for {n} postings from {len(rows)} ranked")
    picked = [rows[i * len(rows) // n] for i in range(n)]
    picked.sort(key=lambda row: hashlib.sha256(str(row.id).encode()).digest())
    entries = [
        {
            "posting_id": row.id,
            "rating": None,
            "text_hash": text_hash(posting_text(row)),
            "title": row.title,
            "company": row.company,
        }
        for row in picked
    ]
    return {"postings": entries}


async def _score(
    settings: Settings, args: argparse.Namespace, resume_markdown: str
) -> tuple[int, dict[str, Any] | None]:
    setup = _setup(settings, args, resume_markdown)
    evaluating = args.command == "score-eval-run"
    if evaluating:
        rated = load_rated_hashes(args.ratings)
        load_ratings(args.ratings)
    engine = build_engine(settings)
    sessions = build_session_factory(engine)
    try:
        async with sessions() as session, session.begin():
            run_id = await start_run(session, settings.run_budget_usd)
            rules = rules_from_settings(settings)
            if evaluating:
                await select_for_analysis(session, sorted(rated))
            else:
                await auto_forward(session, n=settings.auto_forward_top_n, rules=rules)
        status = "failed"
        try:
            async with build_provider(settings, setup.provider) as provider:
                client = LLMClient(
                    sessions, provider, settings, allowed_tools=frozenset({SCORE_TOOL.name})
                )
                if evaluating:
                    summary, scores = await score_for_eval(
                        sessions, client, rated, run_id=run_id, setup=setup
                    )
                else:
                    summary = await score_selected(sessions, client, run_id=run_id, setup=setup)
            status = "done"
        finally:
            async with sessions() as session, session.begin():
                await finish_run(session, run_id, status)
    finally:
        await engine.dispose()
    print(
        f"run {run_id}: scored {summary.scored}, already scored {summary.already_scored}, "
        f"failed {summary.failed}, not reached {summary.remaining}"
    )
    code = 2 if summary.remaining else 0
    if not evaluating:
        return code, None
    recorded = {
        "prompt_version": setup.prompt_version,
        "provider": setup.provider,
        "model": setup.model,
        "scores": {str(posting_id): scores.get(posting_id) for posting_id in sorted(rated)},
    }
    return code, recorded


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")


def run_stage2_command(args: argparse.Namespace) -> int:
    quiet_sdk_loggers()
    settings = get_settings()
    try:
        if args.command == "rank":
            return asyncio.run(_rank(settings, args.resume.read_text(encoding="utf-8")))
        if args.command == "shortlist":
            return asyncio.run(_shortlist(settings))
        if args.command == "select":
            return asyncio.run(_select(settings, args.posting_ids))
        if args.command == "sample-score-eval":
            _write_json(args.out, asyncio.run(_sample(settings, args.n)))
            print("set each rating to 1-5 before running score-eval-run")
            return 0
        resume_markdown = args.resume.read_text(encoding="utf-8")
        code, recorded = asyncio.run(_score(settings, args, resume_markdown))
        if recorded is not None:
            _write_json(args.out, recorded)
        return code
    except LLMError as exc:
        print(f"model call failed: {exc} (status {exc.status})", file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError, ValueError, KeyError) as exc:
        print(f"failed: {exc!r}", file=sys.stderr)
        return 1
