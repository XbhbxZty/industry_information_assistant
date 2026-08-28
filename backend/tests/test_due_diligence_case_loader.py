"""Focused regressions for the canonical factoring simulation case contracts."""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"
for _path in (str(BACKEND_DIR), str(APP_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from eval.due_diligence_oracle import (  # noqa: E402
    OracleLoadError,
    load_due_diligence_oracle,
)
from service import due_diligence_case as case_module  # noqa: E402
from service.due_diligence_case import (  # noqa: E402
    CasePackageError,
    CaseValidationError,
    DueDiligenceCase,
    EnterpriseClaim,
    SCHEMA_VERSION,
    SourceOutcome,
    SourceQueryResult,
    is_valid_unified_credit_code,
    load_due_diligence_case,
    validate_loaded_due_diligence_case,
)


_USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_MATERIAL_BYTES = b"fictional material only"


def _valid_uscc(prefix: str = "91350211M000100Y4") -> str:
    assert len(prefix) == 17
    total = sum(_USCC_CHARS.index(char) * weight for char, weight in zip(prefix, _USCC_WEIGHTS))
    return prefix + _USCC_CHARS[(31 - total % 31) % 31]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _payloads() -> dict[str, object]:
    return {
        "manifest": {
            "schema_version": SCHEMA_VERSION,
            "case_id": "case-001",
            "title": "纯虚构保理最小案例",
            "simulation_only": True,
            "fictional_notice": "所有主体和交易均为纯虚构测试数据。",
            "as_of": "2026-07-01",
            "cutoff_date": "2026-06-30",
            "business_type": "有追索权国内保理",
            "scenario": "用于验证严格仿真案例加载边界。",
            "primary_subject_id": "supplier-001",
            "input": {
                "application": "input/structured/application.json",
                "entities": "input/structured/entities.json",
                "claims": "input/structured/claims.jsonl",
                "sources": "input/structured/sources.json",
                "materials": "input/structured/materials.json",
            },
        },
        "application": {
            "application_id": "application-001",
            "application_date": "2026-06-15",
            "primary_borrower_id": "supplier-001",
            "applicant_supplier_id": "supplier-001",
            "debtor_entity_id": "debtor-001",
            "requested_amount": {"value": 4000000, "unit": "CNY"},
            "requested_term": {"value": 90, "unit": "DAYS"},
            "financing_purpose": "采购原材料",
            "receivable_face_amount": {"value": 5000000, "unit": "CNY"},
            "advance_rate": {"value": 80, "unit": "PERCENT"},
            "contract_number": "contract-001",
            "payment_due_date": "2026-08-27",
            "trade_period": {"start_date": "2026-05-08", "end_date": "2026-05-29"},
        },
        "entities": {
            "entities": [
                {
                    "entity_id": "supplier-001",
                    "name": "供应商【纯虚构】",
                    "entity_kind": "enterprise",
                    "unified_credit_code": _valid_uscc(),
                    "roles": [
                        "applicant_supplier",
                        "primary_borrower",
                        "contract_party",
                        "invoice_issuer",
                    ],
                },
                {
                    "entity_id": "debtor-001",
                    "name": "付款方【纯虚构】",
                    "entity_kind": "enterprise",
                    "unified_credit_code": _valid_uscc("91350211M000100Y5"),
                    "roles": ["account_debtor", "contract_party", "payer"],
                },
            ],
            "relationships": [
                {
                    "relationship_id": "relationship-001",
                    "from_entity_id": "debtor-001",
                    "to_entity_id": "supplier-001",
                    "relationship_type": "account_debtor_of",
                    "as_of_date": "2026-06-29",
                    "supporting_material_ids": ["material-001"],
                }
            ],
        },
        "claims": [
            {
                "claim_id": "claim-001",
                "field_id": "receivable_amount",
                "subject_entity_id": "supplier-001",
                "status": "claimed",
                "asserted_by": "application",
                "as_of_date": "2026-06-29",
                "assertion": "应收账款真实、合法且可转让。",
                "supporting_material_ids": ["material-001"],
            }
        ],
        "sources": {
            "sources": [
                {
                    "source_id": "source-001",
                    "source_channel": "public",
                    "source_type": "registry",
                    "issuer": "模拟登记查询",
                    "subject_entity_id": "supplier-001",
                    "query_scope": "receivable_amount",
                }
            ],
            "query_results": [
                {
                    "source_id": "source-001",
                    "outcome": "success_no_record",
                    "queried_field_ids": ["receivable_amount"],
                    "retrieved_at": "2026-07-02T08:00:00Z",
                    "observed_at": "2026-06-30",
                    "records": [],
                }
            ],
        },
        "materials": [
            {
                "material_id": "material-001",
                "subject_entity_id": "supplier-001",
                "material_type": "contract",
                "relative_path": "input/documents/contract.txt",
                "provenance": "申请人提交的纯虚构合同材料",
                "source_channel": "enterprise_submitted",
                "issuer": "供应商【纯虚构】",
                "document_date": "2026-06-29",
                "sha256": hashlib.sha256(_MATERIAL_BYTES).hexdigest(),
            }
        ],
        "oracle": {
            "schema_version": SCHEMA_VERSION,
            "case_id": "case-001",
            "expected_decision": "conditional_approve",
            "conditions": ["放款前复核登记查询。"],
            "maximum_advance": {"value": 4000000, "unit": "CNY"},
            "known_conflicts": [],
            "expected_claim_verdicts": [
                {
                    "claim_id": "claim-001",
                    "verdict": "supported",
                    "source_refs": ["source-001"],
                    "rationale": "纯虚构评测答案。",
                }
            ],
            "rationale": "纯虚构评测结论。",
        },
    }


def _write_case(tmp_path: Path, payloads: dict[str, object] | None = None) -> dict[str, object]:
    payloads = payloads or _payloads()
    _write_json(tmp_path / "manifest.json", payloads["manifest"])
    _write_json(tmp_path / "input/structured/application.json", payloads["application"])
    _write_json(tmp_path / "input/structured/entities.json", payloads["entities"])
    claims_path = tmp_path / "input/structured/claims.jsonl"
    claims_path.parent.mkdir(parents=True, exist_ok=True)
    claims_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in payloads["claims"]),
        encoding="utf-8",
    )
    _write_json(tmp_path / "input/structured/sources.json", payloads["sources"])
    _write_json(tmp_path / "input/structured/materials.json", payloads["materials"])
    material_path = tmp_path / "input/documents/contract.txt"
    material_path.parent.mkdir(parents=True, exist_ok=True)
    material_path.write_bytes(_MATERIAL_BYTES)
    _write_json(tmp_path / "oracle/expected.json", payloads["oracle"])
    return payloads


def _reload(tmp_path: Path, payloads: dict[str, object]) -> None:
    _write_case(tmp_path, payloads)


def _make_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this Windows host: {exc}")


def test_canonical_loader_is_oracle_blind_and_does_not_import_eval(tmp_path: Path):
    _write_case(tmp_path)
    # The production loader must succeed without parsing even malformed oracle bytes.
    (tmp_path / "oracle/expected.json").write_text("not JSON", encoding="utf-8")
    case = load_due_diligence_case(tmp_path)

    assert case.manifest.schema_version == SCHEMA_VERSION
    assert case.query_results[0].establishes_no_record
    assert "oracle" not in case.model_dump_json().lower()
    assert not hasattr(case, "oracle")
    imports = [
        node.module or ""
        for node in ast.walk(ast.parse(Path(case_module.__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    ]
    assert not any(module == "eval" or module.startswith("eval.") for module in imports)


def test_loader_issues_exact_case_capability_and_rejects_copies_or_mutations(tmp_path: Path):
    _write_case(tmp_path)
    case = load_due_diligence_case(tmp_path)
    assert validate_loaded_due_diligence_case(case) is case

    copied = DueDiligenceCase(
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
        validate_loaded_due_diligence_case(copied)

    object.__setattr__(case, "sources", ())
    with pytest.raises(CaseValidationError, match="no longer matches"):
        validate_loaded_due_diligence_case(case)


def test_manifest_is_canonical_strict_and_rejects_wire_coercion(tmp_path: Path):
    payloads = _payloads()
    payloads["manifest"]["simulation_only"] = "true"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="simulation_only"):
        load_due_diligence_case(tmp_path)

    payloads = _payloads()
    payloads["application"]["requested_amount"]["value"] = "4000000"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="requested_amount"):
        load_due_diligence_case(tmp_path)

    payloads = _payloads()
    payloads["manifest"]["oracle"] = "oracle/expected.json"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="oracle"):
        load_due_diligence_case(tmp_path)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../oracle/expected.json",
        "input/../oracle/expected.json",
        "C:/secret/expected.json",
        "//server/share/expected.json",
        "input\\structured\\application.json",
    ],
)
def test_manifest_path_traversal_windows_and_unc_are_rejected(tmp_path: Path, bad_path: str):
    payloads = _payloads()
    payloads["manifest"]["input"]["application"] = bad_path  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError):
        load_due_diligence_case(tmp_path)


def test_claim_is_claimed_only_and_measurements_allow_negative_or_zero():
    base = _payloads()["claims"][0].copy()  # type: ignore[union-attr]
    base["status"] = "verified"
    with pytest.raises(ValidationError, match="claimed"):
        EnterpriseClaim.model_validate(base)

    measured = _payloads()["claims"][0].copy()  # type: ignore[union-attr]
    measured.pop("assertion")
    measured["measurement"] = {"value": 0, "unit": "COUNT"}
    # Direct Python validation is intentionally strict; model_validate_json is the wire API.
    claim = EnterpriseClaim.model_validate_json(json.dumps(measured))
    assert claim.measurement and claim.measurement.value == 0

    measured["measurement"] = {"value": -12, "unit": "CNY"}
    claim = EnterpriseClaim.model_validate_json(json.dumps(measured))
    assert claim.measurement and claim.measurement.value == -12


@pytest.mark.parametrize(
    "outcome",
    [
        SourceOutcome.SUBJECT_NOT_FOUND,
        SourceOutcome.NOT_QUERIED,
        SourceOutcome.NOT_PROVIDED,
        SourceOutcome.UNAUTHORIZED,
        SourceOutcome.TIMEOUT,
        SourceOutcome.SOURCE_UNAVAILABLE,
    ],
)
def test_every_unknown_source_outcome_is_explicit_and_never_negative(outcome: SourceOutcome):
    data = {
        "source_id": "source-001",
        "outcome": outcome.value,
        "queried_field_ids": ["receivable_amount"],
        "records": [],
        "outcome_detail": "信息缺口明确记录。",
    }
    result = SourceQueryResult.model_validate_json(json.dumps(data))
    assert not result.is_success
    assert not result.establishes_no_record
    data["records"] = [{"safe": "but invalid for an unknown outcome"}]
    with pytest.raises(ValidationError, match="must not carry records"):
        SourceQueryResult.model_validate_json(json.dumps(data))


def test_success_results_require_dates_and_only_empty_success_is_negative():
    records = {
        "source_id": "source-001",
        "outcome": "success_with_records",
        "queried_field_ids": ["receivable_amount"],
        "records": [{"nested": {"amount": 4}}],
    }
    with pytest.raises(ValidationError, match="retrieved_at"):
        SourceQueryResult.model_validate_json(json.dumps(records))
    records.update({"retrieved_at": "2026-07-10T08:00:00Z", "as_of_date": "2026-06-30"})
    with_records = SourceQueryResult.model_validate_json(json.dumps(records))
    assert with_records.is_success and not with_records.establishes_no_record
    assert '"amount":4' in with_records.model_dump_json()
    with pytest.raises(TypeError):
        with_records.records[0]["nested"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        with_records.records[0]["nested"]["amount"] = 5  # type: ignore[index]

    no_record = {
        "source_id": "source-001",
        "outcome": "success_no_record",
        "queried_field_ids": ["receivable_amount"],
        "records": [],
    }
    with pytest.raises(ValidationError, match="retrieved_at and observed_at"):
        SourceQueryResult.model_validate_json(json.dumps(no_record))
    no_record.update({"retrieved_at": "2026-07-10T08:00:00Z", "observed_at": "2026-06-30"})
    assert SourceQueryResult.model_validate_json(json.dumps(no_record)).establishes_no_record

    records["retrieved_at"] = "2026-07-10T08:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        SourceQueryResult.model_validate_json(json.dumps(records))


def test_material_requires_a_date_or_explicit_unknown_reason(tmp_path: Path):
    payloads = _payloads()
    payloads["materials"][0].pop("document_date")  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="date_unknown_reason"):
        load_due_diligence_case(tmp_path)

    payloads["materials"][0]["date_unknown_reason"] = "原始材料未载明日期。"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    material = load_due_diligence_case(tmp_path).materials[0]
    assert material.date_unknown_reason == "原始材料未载明日期。"

    payloads["materials"][0]["observed_at"] = "2026-06-29"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="only when all factual dates are unknown"):
        load_due_diligence_case(tmp_path)


def test_records_recursively_reject_evaluation_labels():
    payload = {
        "source_id": "source-001",
        "outcome": "success_with_records",
        "queried_field_ids": ["receivable_amount"],
        "retrieved_at": "2026-07-01T00:00:00Z",
        "observed_at": "2026-06-30",
        "records": [{"nested": {"expected_decision": "reject"}}],
    }
    with pytest.raises(ValidationError, match="reserved evaluation-only"):
        SourceQueryResult.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    "mutator, expected_message",
    [
        (
            lambda data: data["entities"]["entities"].append(data["entities"]["entities"][0].copy()),
            "duplicate entity",
        ),
        (
            lambda data: data["entities"]["relationships"].append(
                data["entities"]["relationships"][0].copy()
            ),
            "duplicate relationship",
        ),
        (
            lambda data: data["entities"]["relationships"][0].update(
                {"from_entity_id": "missing-entity"}
            ),
            "unknown from_entity",
        ),
        (
            lambda data: data["claims"][0].update({"subject_entity_id": "missing-entity"}),
            "unknown entity",
        ),
        (
            lambda data: data["sources"]["query_results"][0].update(
                {"queried_field_ids": ["unknown_field"]}
            ),
            "unknown field_id",
        ),
        (
            lambda data: data["sources"]["query_results"].clear(),
            "exactly one explicit outcome",
        ),
        (
            lambda data: data["materials"][0].update({"sha256": "0" * 64}),
            "sha256 does not match",
        ),
    ],
)
def test_cross_file_references_duplicates_and_hashes_are_validated(
    tmp_path: Path, mutator, expected_message: str
):
    payloads = _payloads()
    mutator(payloads)
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match=expected_message):
        load_due_diligence_case(tmp_path)


def test_facts_and_periods_stop_at_cutoff_but_retrieval_time_does_not(tmp_path: Path):
    payloads = _payloads()
    payloads["sources"]["query_results"][0]["retrieved_at"] = "2030-01-01T00:00:00Z"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    assert load_due_diligence_case(tmp_path).manifest.cutoff_date.isoformat() == "2026-06-30"

    payloads = _payloads()
    payloads["sources"]["query_results"][0]["observed_at"] = "2026-07-01"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="cutoff_date"):
        load_due_diligence_case(tmp_path)

    payloads = _payloads()
    payloads["entities"]["relationships"][0]["as_of_date"] = "2026-07-01"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="relationship.*cutoff_date"):
        load_due_diligence_case(tmp_path)


def test_uscc_checksum_and_application_role_references_are_enforced(tmp_path: Path):
    assert is_valid_unified_credit_code(_valid_uscc())
    payloads = _payloads()
    payloads["entities"]["entities"][0]["unified_credit_code"] = "0" * 18  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="USCC"):
        load_due_diligence_case(tmp_path)

    payloads = _payloads()
    payloads["entities"]["entities"][1]["roles"] = ["payer"]  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(CaseValidationError, match="account_debtor"):
        load_due_diligence_case(tmp_path)


def test_oracle_is_single_file_and_must_match_production_case(tmp_path: Path):
    _write_case(tmp_path)
    case = load_due_diligence_case(tmp_path)
    oracle = load_due_diligence_oracle(tmp_path, case)
    assert oracle.expected_decision.value == "conditional_approve"
    assert oracle.maximum_advance and oracle.maximum_advance.value == 4000000

    payloads = _payloads()
    payloads["oracle"]["case_id"] = "other-case"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="case_id"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))

    payloads = _payloads()
    payloads["oracle"]["expected_claim_verdicts"][0]["source_refs"] = ["unknown-source"]  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="unknown sources"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))


def test_oracle_conflicts_and_conditional_terms_are_strictly_validated(tmp_path: Path):
    payloads = _payloads()
    payloads["oracle"]["known_conflicts"] = [  # type: ignore[index]
        {
            "conflict_id": "conflict-001",
            "claim_id": "claim-001",
            "assertion_source": "source-001",
            "contrary_source_ids": ["source-001"],
            "description": "申请材料和查询记录出现冲突。",
            "rationale": "评分器必须要求解释这个冲突。",
        }
    ]
    _write_case(tmp_path, payloads)
    assert len(load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path)).known_conflicts) == 1

    payloads = _payloads()
    payloads["oracle"]["known_conflicts"] = [  # type: ignore[index]
        {
            "conflict_id": "conflict-001",
            "claim_id": "unknown-claim",
            "assertion_source": "source-001",
            "contrary_source_ids": ["source-001"],
            "description": "冲突。",
            "rationale": "理由。",
        }
    ]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="unknown claim"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))

    payloads = _payloads()
    conflict = {
        "conflict_id": "conflict-001",
        "claim_id": "claim-001",
        "assertion_source": "source-001",
        "contrary_source_ids": ["source-001"],
        "description": "重复 ID 测试。",
        "rationale": "必须拒绝重复冲突。",
    }
    payloads["oracle"]["known_conflicts"] = [conflict, conflict.copy()]  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="duplicate oracle conflict"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))

    payloads = _payloads()
    payloads["oracle"]["conditions"] = []  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="conditional_approve requires"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))

    payloads = _payloads()
    payloads["oracle"]["expected_decision"] = "reject"  # type: ignore[index]
    _write_case(tmp_path, payloads)
    with pytest.raises(OracleLoadError, match="only conditional_approve"):
        load_due_diligence_oracle(tmp_path, load_due_diligence_case(tmp_path))


def test_manifest_input_material_and_oracle_symlinks_cannot_cross_zones(tmp_path: Path):
    _write_case(tmp_path)
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "expected.json").write_text("{}", encoding="utf-8")

    # ``input -> oracle`` is rejected before a manifest-declared file can open.
    shutil.rmtree(tmp_path / "input")
    _make_symlink_or_skip(tmp_path / "input", tmp_path / "oracle")
    with pytest.raises(CasePackageError, match="input/"):
        load_due_diligence_case(tmp_path)


def test_manifest_material_and_oracle_file_symlinks_are_rejected(tmp_path: Path):
    _write_case(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.unlink()
    _make_symlink_or_skip(manifest, tmp_path / "oracle/expected.json")
    with pytest.raises(CasePackageError, match="regular root manifest"):
        load_due_diligence_case(tmp_path)

    _write_case(tmp_path)
    material = tmp_path / "input/documents/contract.txt"
    material.unlink()
    _make_symlink_or_skip(material, tmp_path / "oracle/expected.json")
    with pytest.raises(CasePackageError, match="declared input file"):
        load_due_diligence_case(tmp_path)

    _write_case(tmp_path)
    production_case = load_due_diligence_case(tmp_path)
    shutil.rmtree(tmp_path / "oracle")
    _make_symlink_or_skip(tmp_path / "oracle", tmp_path.parent)
    with pytest.raises(OracleLoadError, match="oracle/"):
        load_due_diligence_oracle(tmp_path, production_case)
