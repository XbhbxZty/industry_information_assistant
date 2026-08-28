"""Regression tests for the canonical-case structured evidence bridge."""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
for _path in (str(BACKEND_DIR), str(APP_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from config.dd_checklist import NO_RECORD_VALUE  # noqa: E402
from service.due_diligence_case import (  # noqa: E402
    CaseValidationError,
    DueDiligenceCase,
    SourceChannel,
    load_due_diligence_case,
)
import service.due_diligence_case_evidence as bridge_module  # noqa: E402
from service.due_diligence_case_evidence import (  # noqa: E402
    ADAPTER_ID,
    CANONICAL_FIELD_POLICY,
    build_case_evidence,
    verify_case_evidence,
)
from service.risk_scorecard import PROFILE_BACKED_FIELDS  # noqa: E402


CASE_ROOT = BACKEND_DIR / "eval" / "simulation_cases"


def _case(name: str):
    return load_due_diligence_case(CASE_ROOT / name)


def _copied_case(tmp_path: Path, name: str) -> Path:
    copied = tmp_path / name
    shutil.copytree(CASE_ROOT / name, copied)
    return copied


def _load_with_sources(tmp_path: Path, name: str, mutate) -> object:
    copied = _copied_case(tmp_path, name)
    path = copied / "input" / "structured" / "sources.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return load_due_diligence_case(copied)


def _load_with_claims(tmp_path: Path, name: str, mutate) -> object:
    copied = _copied_case(tmp_path, name)
    path = copied / "input" / "structured" / "claims.jsonl"
    payload = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutate(payload)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in payload),
        encoding="utf-8",
    )
    return load_due_diligence_case(copied)


def _check(bridge, canonical_field_id: str):
    return next(
        check for check in bridge.field_checks
        if check["canonical_field_id"] == canonical_field_id
    )


def _evidence(bridge, check):
    assert check["evidence_ids"]
    return bridge.evidence_store[check["evidence_ids"][0]]


@pytest.mark.parametrize(
    ("name", "expected", "evidence_count"),
    [
        (
            "case_a_consistent",
            {
                "entity_registration_status": "verified",
                "receivable_face_amount": "unverified",
                "delivery_and_acceptance": "verified",
                "debtor_confirmation": "verified",
                "prior_transfer_or_pledge": "verified",
                "undisclosed_financing": "verified",
            },
            5,
        ),
        (
            "case_b_hidden_financing",
            {
                "receivable_face_amount": "unverified",
                "delivery_and_acceptance": "verified",
                "undisclosed_financing": "conflicting",
                "movable_mortgage": "conflicting",
                "prior_transfer_or_pledge": "verified",
            },
            4,
        ),
        (
            "case_c_receivable_authenticity",
            {
                "receivable_face_amount": "unverified",
                "delivery_and_acceptance": "conflicting",
                "debtor_confirmation": "conflicting",
                "prior_transfer_or_pledge": "conflicting",
            },
            3,
        ),
        (
            "case_d_insufficient_data",
            {
                "entity_registration_status": "unverified",
                "legal_credit_record": "unverified",
                "undisclosed_financing": "unverified",
                "movable_mortgage": "unverified",
                "receivable_face_amount": "unverified",
                "prior_transfer_or_pledge": "unverified",
                "site_operations": "unverified",
            },
            0,
        ),
    ],
)
def test_four_cases_have_conservative_deterministic_outcomes(name, expected, evidence_count):
    case = _case(name)
    bridge = build_case_evidence(case)

    assert {check["canonical_field_id"]: check["status"] for check in bridge.field_checks} == expected
    assert len(bridge.evidence_store) == evidence_count
    # The canonical claim itself is never upgraded or rewritten.
    assert all(claim.status == "claimed" for claim in case.claims)

    report = verify_case_evidence(bridge)
    assert report.ok, report.mismatches
    assert not report.degradations


def test_claim_checks_are_scoped_and_keep_assertions_out_of_verified_value():
    case = _case("case_a_consistent")
    bridge = build_case_evidence(case)
    receivable = _check(bridge, "receivable_face_amount")

    assert receivable["status"] == "unverified"
    assert receivable["value"] is None
    assert str(receivable["claimed_value"]["value"]) == "5000000"
    assert receivable["supporting_material_ids"] == ["a-material-transaction"]
    assert receivable["field_id"] == (
        f"case.{case.manifest.case_id}.claim.a-claim-receivable.subject.ent-a-supplier"
    )
    assert not receivable["evidence_ids"], "申请金额主张不得自身成为 evidence"


def test_same_canonical_field_on_another_subject_cannot_borrow_evidence(tmp_path: Path):
    case = _load_with_claims(
        tmp_path,
        "case_a_consistent",
        lambda claims: claims.append({
            **next(item for item in claims if item["field_id"] == "delivery_and_acceptance"),
            "claim_id": "a-claim-delivery-other-subject",
            "subject_entity_id": "ent-a-supplier",
        }),
    )
    delivery = next(claim for claim in case.claims if claim.claim_id == "a-claim-delivery")
    bridge = build_case_evidence(case)
    checks = [check for check in bridge.field_checks if check["canonical_field_id"] == "delivery_and_acceptance"]
    assert len(checks) == 2
    assert checks[0]["field_id"] != checks[1]["field_id"]

    by_subject = {check["subject_entity_id"]: check for check in checks}
    assert by_subject[delivery.subject_entity_id]["status"] == "verified"
    wrong_subject = by_subject[case.loan_application.applicant_supplier_id]
    assert wrong_subject["status"] == "unverified"
    assert not wrong_subject["source_outcomes"]
    assert not wrong_subject["evidence_ids"]


def test_application_declarations_and_applicant_signed_material_cannot_self_prove():
    bridge = build_case_evidence(_case("case_c_receivable_authenticity"))
    receivable = _check(bridge, "receivable_face_amount")

    assert receivable["status"] == "unverified"
    roles = {entry["source_id"]: entry["independence"] for entry in receivable["source_outcomes"]}
    assert roles["c-src-application-supplier"] == "assertion"
    assert roles["c-src-contract"] == "assertion"
    assert not receivable["evidence_ids"]


def test_loader_rejects_enterprise_submitted_issuer_text_spoof(tmp_path: Path):
    with pytest.raises(CaseValidationError, match="issuer must exactly match"):
        _load_with_sources(
            tmp_path,
            "case_a_consistent",
            lambda payload: next(
                source for source in payload["sources"]
                if source["source_id"] == "a-src-acceptance"
            ).update({"issuer": "未登记签发方【纯虚构】"}),
        )


def test_enterprise_submitted_without_loader_bound_issuer_id_fails_closed(tmp_path: Path):
    case = _load_with_sources(
        tmp_path,
        "case_a_consistent",
        lambda payload: next(
            source for source in payload["sources"]
            if source["source_id"] == "a-src-acceptance"
        ).pop("issuer_entity_id"),
    )
    bridge = build_case_evidence(case)

    delivery = _check(bridge, "delivery_and_acceptance")
    assert delivery["status"] == "unverified"
    assert not delivery["evidence_ids"]
    assert "issuer" in delivery["failure_reason"]


def test_multiple_sources_are_aggregated_once_not_superseded():
    bridge = build_case_evidence(_case("case_a_consistent"))
    financing = _check(bridge, "undisclosed_financing")
    evidence = _evidence(bridge, financing)

    assert evidence["source_adapter"] == ADAPTER_ID
    assert evidence["supersedes"] == []
    assert evidence.get("superseded_by") is None
    assert financing["evidence_ids"] == [evidence["evidence_id"]]
    assert {item["source"]["source_id"] for item in evidence["raw"]["query_results"]} == {
        "a-src-bank", "a-src-credit"
    }
    # Latest query time and latest fact time use the case values, not the run clock.
    assert financing["retrieved_at"] == "2026-07-01T08:20:00+00:00"
    assert financing["as_of_date"] == "2026-06-30"


def test_successful_but_unaccepted_later_result_cannot_refresh_evidence_dates(tmp_path: Path):
    def mutate(payload):
        result = next(
            item for item in payload["query_results"] if item["source_id"] == "a-src-contract"
        )
        result["queried_field_ids"].append("undisclosed_financing")
        result["retrieved_at"] = "2026-07-02T00:00:00Z"

    bridge = build_case_evidence(_load_with_sources(tmp_path, "case_a_consistent", mutate))
    financing = _check(bridge, "undisclosed_financing")
    evidence = _evidence(bridge, financing)

    assert financing["retrieved_at"] == "2026-07-01T08:20:00+00:00"
    assert financing["as_of_date"] == "2026-06-30"
    assert {item["source_id"] for item in evidence["raw"]["query_results"]} == {
        "a-src-contract", "a-src-bank", "a-src-credit",
    }


def test_later_application_assertion_cannot_refresh_conflicting_evidence_dates(tmp_path: Path):
    bridge = build_case_evidence(_load_with_sources(
        tmp_path,
        "case_b_hidden_financing",
        lambda payload: next(
            item for item in payload["query_results"] if item["source_id"] == "b-src-application"
        ).update({"retrieved_at": "2026-07-02T00:00:00Z"}),
    ))
    financing = _check(bridge, "undisclosed_financing")

    assert financing["status"] == "conflicting"
    assert financing["retrieved_at"] == "2026-07-01T09:18:00+00:00"
    assert financing["as_of_date"] == "2026-06-30"


def test_no_record_requires_exact_registry_scope_and_keeps_standard_value(tmp_path: Path):
    case = _case("case_b_hidden_financing")
    valid = build_case_evidence(case)
    valid_check = _check(valid, "prior_transfer_or_pledge")
    assert valid_check["status"] == "verified"
    assert valid_check["value"] == NO_RECORD_VALUE
    valid_evidence = _evidence(valid, valid_check)
    transfer_raw = next(
        item for item in valid_evidence["raw"]["query_results"]
        if item["source_id"] == "b-src-transfer"
    )
    assert transfer_raw["query_scope"] == next(
        source.query_scope for source in case.sources if source.source_id == "b-src-transfer"
    )

    blocked = build_case_evidence(_load_with_sources(
        tmp_path,
        "case_b_hidden_financing",
        lambda payload: next(
            source for source in payload["sources"] if source["source_id"] == "b-src-transfer"
        ).update({
            "query_scope": f"仅查询 X{case.loan_application.contract_number}Y 的其他登记",
        }),
    ))
    blocked_check = _check(blocked, "prior_transfer_or_pledge")
    assert blocked_check["status"] == "unverified"
    assert not blocked_check["evidence_ids"]
    assert "精确范围规则" in blocked_check["failure_reason"]


def test_transaction_record_with_wrong_contract_is_not_normalized(tmp_path: Path):
    bridge = build_case_evidence(_load_with_sources(
        tmp_path,
        "case_c_receivable_authenticity",
        lambda payload: next(
            item for item in payload["query_results"] if item["source_id"] == "c-src-reconciliation"
        )["records"][0].update({"contract_number": "FICT-C-OTHER-CONTRACT【纯虚构】"}),
    ))
    delivery = _check(bridge, "delivery_and_acceptance")

    assert delivery["status"] == "unverified"
    assert not delivery["evidence_ids"]
    assert "未绑定" in delivery["failure_reason"]


def test_application_declaration_with_wrong_application_id_is_not_normalized(tmp_path: Path):
    def mutate(payload):
        declaration = next(
            item for item in payload["query_results"] if item["source_id"] == "b-src-application"
        )
        declaration["records"][0]["application_id"] = "sim-fac-b-other-application"
        mortgage = next(
            item for item in payload["query_results"] if item["source_id"] == "b-src-mortgage"
        )
        mortgage.update({
            "outcome": "not_queried",
            "outcome_detail": "temporary fixture leaves only the declaration",
            "records": [],
        })

    bridge = build_case_evidence(_load_with_sources(tmp_path, "case_b_hidden_financing", mutate))
    mortgage = _check(bridge, "movable_mortgage")

    assert mortgage["status"] == "unverified"
    assert not mortgage["evidence_ids"]
    assert "application_id" in mortgage["failure_reason"]


def test_unknown_outcomes_stay_visible_without_creating_evidence_or_false_attempts():
    bridge = build_case_evidence(_case("case_d_insufficient_data"))
    assert bridge.evidence_store == {}

    for check in bridge.field_checks:
        kinds = {entry["outcome"] for entry in check["source_outcomes"]}
        not_done = {
            entry["source_id"] for entry in check["source_outcomes"]
            if entry["outcome"] in {"not_queried", "not_provided"}
        }
        assert not not_done.intersection(check["attempted_sources"])
        if kinds:
            assert check["failure_reason"]
    legal = _check(bridge, "legal_credit_record")
    assert legal["source_outcomes"][0]["outcome"] == "not_queried"
    assert "未执行查询" in legal["failure_reason"]


def test_bridge_has_no_profile_patch_or_core_scoring_side_effect():
    bridge = build_case_evidence(_case("case_b_hidden_financing"))

    assert all(check["field_id"] not in PROFILE_BACKED_FIELDS for check in bridge.field_checks)
    assert all(evidence["profile_patch"] == {} for evidence in bridge.evidence_store.values())
    assert all(check["canonical_field_id"] in CANONICAL_FIELD_POLICY for check in bridge.field_checks)
    assert all(check["scope"] == "canonical:factoring" for check in bridge.field_checks)


def test_module_and_evidence_are_blind_to_hidden_evaluation_data():
    source = Path(bridge_module.__file__).read_text(encoding="utf-8")
    assert "oracle" not in source.lower()
    imported = [node.module or "" for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
    assert not any(
        module == "eval" or module.startswith(("eval.", "router", "graph", "db", "scorecard"))
        for module in imported
    )

    bridge = build_case_evidence(_case("case_c_receivable_authenticity"))
    raw = json.dumps(bridge.evidence_store, ensure_ascii=False).lower()
    for canary in ("expected_decision", "expected_claim_verdicts", "known_conflicts", "verdict"):
        assert canary not in raw


def test_app_namespace_import_is_selected_when_backend_and_app_are_both_importable():
    script = "\n".join((
        "import sys",
        f"sys.path[0:0] = [{str(BACKEND_DIR)!r}, {str(APP_DIR)!r}]",
        "from app.service.due_diligence_case import load_due_diligence_case",
        "import app.service.due_diligence_case_evidence as evidence",
        f"case = load_due_diligence_case({str(CASE_ROOT / 'case_a_consistent')!r})",
        "bridge = evidence.build_case_evidence(case)",
        "assert evidence.__package__ == 'app.service'",
        "assert evidence.DueDiligenceCase.__module__.startswith('app.')",
        "assert evidence.verify_case_evidence(bridge).ok",
    ))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_model_copy_attack_cannot_reclassify_application_material_as_independent():
    case = _case("case_b_hidden_financing")
    altered_sources = tuple(
        source.model_copy(update={
            "issuer": "峻河仓储装备（天津）有限公司【纯虚构】",
            "issuer_entity_id": "ent-b-supplier",
            "source_type": "bank_statement",
            "source_channel": SourceChannel.AUTHORIZED,
        }) if source.source_id == "b-src-application" else source
        for source in case.sources
    )
    attacked = case.model_copy(update={"sources": altered_sources})

    with pytest.raises(CaseValidationError, match="not issued"):
        build_case_evidence(attacked)


def test_publicly_constructed_valid_looking_case_is_not_a_loader_capability():
    case = _case("case_a_consistent")
    constructed = DueDiligenceCase(
        manifest=case.manifest,
        loan_application=case.loan_application,
        entities=case.entities,
        relationships=case.relationships,
        claims=case.claims,
        sources=case.sources,
        query_results=case.query_results,
        materials=case.materials,
    )

    with pytest.raises(CaseValidationError, match="not issued"):
        build_case_evidence(constructed)


def test_object_setattr_semantic_mutation_invalidates_loader_case_seal():
    case = _case("case_a_consistent")
    altered_sources = tuple(
        source.model_copy(update={"source_channel": SourceChannel.AUTHORIZED})
        if source.source_id == "a-src-acceptance" else source
        for source in case.sources
    )
    object.__setattr__(case, "sources", altered_sources)

    with pytest.raises(CaseValidationError, match="no longer matches"):
        build_case_evidence(case)


def test_adapter_rejects_unvalidated_input_type():
    with pytest.raises(TypeError, match="DueDiligenceCase"):
        build_case_evidence({})
