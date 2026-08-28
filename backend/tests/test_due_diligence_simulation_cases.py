"""Package-level regressions for the four canonical fictional simulation cases."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
for _path in (str(BACKEND_DIR), str(APP_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from eval.due_diligence_oracle import load_due_diligence_oracle  # noqa: E402
from service.due_diligence_case import (  # noqa: E402
    EntityKind,
    SourceOutcome,
    load_due_diligence_case,
)


SIMULATION_ROOT = BACKEND_DIR / "eval" / "simulation_cases"
CASES = (
    ("case_a_consistent", "conditional_approve"),
    ("case_b_hidden_financing", "reject"),
    ("case_c_receivable_authenticity", "reject"),
    ("case_d_insufficient_data", "defer_manual_review"),
)
REAL_CASE_RESIDUE = (
    "宁德时代",
    "美的集团",
    "福耀玻璃",
    "苏宁易购",
    "康美药业",
    "海尔智家",
    "金科地产",
    "华晨汽车",
    "红太阳",
    "浙江鼎力",
    "TCL科技",
    "中利集团",
)
ORACLE_CANARY = (
    '"expected_decision"',
    '"expected_claim_verdicts"',
    '"known_conflicts"',
    '"verdict"',
)


@pytest.mark.parametrize(("directory_name", "expected_decision"), CASES)
def test_simulation_case_and_hidden_oracle_load_together(
    directory_name: str, expected_decision: str
) -> None:
    case_root = SIMULATION_ROOT / directory_name

    production_case = load_due_diligence_case(case_root)
    oracle = load_due_diligence_oracle(case_root, production_case)

    assert production_case.manifest.simulation_only is True
    assert production_case.manifest.cutoff_date <= production_case.manifest.as_of
    assert oracle.case_id == production_case.manifest.case_id
    assert oracle.expected_decision.value == expected_decision
    assert {assessment.claim_id for assessment in oracle.expected_claim_verdicts} == {
        claim.claim_id for claim in production_case.claims
    }
    assert all(entity.name.endswith("【纯虚构】") for entity in production_case.entities)
    assert all(
        entity.unified_credit_code and entity.unified_credit_code.startswith("YFAKE")
        for entity in production_case.entities
        if entity.entity_kind is EntityKind.ENTERPRISE
    )


def test_simulation_inputs_are_fictional_and_oracle_blind() -> None:
    for directory_name, _ in CASES:
        case_root = SIMULATION_ROOT / directory_name
        manifest_text = (case_root / "manifest.json").read_text(encoding="utf-8")
        input_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((case_root / "input").rglob("*"))
            if path.is_file()
        )

        assert "纯虚构" in manifest_text
        assert "oracle" not in manifest_text.lower()
        assert not any(residue in input_text for residue in REAL_CASE_RESIDUE)
        assert all(canary not in input_text for canary in ORACLE_CANARY)
        assert "FICT-" in input_text


def test_insufficient_data_case_preserves_unknown_query_states() -> None:
    production_case = load_due_diligence_case(SIMULATION_ROOT / "case_d_insufficient_data")
    outcomes = {result.outcome for result in production_case.query_results}

    assert SourceOutcome.TIMEOUT in outcomes
    assert SourceOutcome.NOT_QUERIED in outcomes
    assert SourceOutcome.NOT_PROVIDED in outcomes
    assert all(not result.establishes_no_record for result in production_case.query_results)
