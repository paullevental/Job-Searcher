from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_llm_chokepoint


def _tree(root: Path) -> Path:
    (root / "agents/src/offerradar_agents/llm").mkdir(parents=True)
    (root / "agents/tests").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "agents/src/offerradar_agents/llm/client.py").write_text("import anthropic\n")
    return root


def test_clean_tree_passes(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "agents/tests/test_ok.py").write_text("import json\n")
    assert check_llm_chokepoint.main(root) == 0


@pytest.mark.parametrize(
    "rel", ["agents/src/offerradar_agents/x.py", "agents/tests/test_x.py", "scripts/x.py"]
)
def test_sdk_import_outside_llm_trips(tmp_path: Path, rel: str) -> None:
    root = _tree(tmp_path)
    (root / rel).write_text("from openai import OpenAI\n")
    assert check_llm_chokepoint.main(root) == 1


def test_allowlisted_test_may_import_sdk(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "agents/tests/test_llm_anthropic_provider.py").write_text("import anthropic\n")
    assert check_llm_chokepoint.main(root) == 0


def test_missing_llm_dir_fails(tmp_path: Path) -> None:
    (tmp_path / "agents/src/offerradar_agents").mkdir(parents=True)
    assert check_llm_chokepoint.main(tmp_path) == 1
