from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from offerradar_agents.llm.provider import ToolSpec


class ScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    matched_skills: list[str]
    missing_skills: list[str]
    why_fit: str = Field(min_length=1)


SCORE_TOOL: Final = ToolSpec(
    name="record_score",
    description="Record how well the posting fits the candidate's resume.",
    input_schema={
        "type": "object",
        "properties": {
            "score": {"type": "integer", "description": "0 to 100."},
            "matched_skills": {"type": "array", "items": {"type": "string"}},
            "missing_skills": {"type": "array", "items": {"type": "string"}},
            "why_fit": {"type": "string", "description": "At most two sentences."},
        },
        "required": ["score", "matched_skills", "missing_skills", "why_fit"],
        "additionalProperties": False,
    },
)
