"""Bridge validated canonical factoring cases into the structured evidence chain.

This module deliberately consumes an already-loaded :class:`DueDiligenceCase`.
It does not open case packages or turn application assertions into facts.  The
only evidence it writes is a deterministic, claim-scoped projection of a
successful ``SourceQueryResult`` under this module's closed field policy.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

if __package__ and (__package__ == "app.service" or __package__.startswith("app.service.")):
    from app.config.dd_checklist import NO_RECORD_VALUE
    from app.service.due_diligence_case import (
        DueDiligenceCase,
        EnterpriseClaim,
        SourceChannel,
        SourceDescriptor,
        SourceOutcome,
        SourceQueryResult,
        validate_loaded_due_diligence_case,
    )
    from app.service.verification import (
        ReplayReport,
        parse_iso,
        record_structured_evidence,
        register_adapter,
        verify_evidence_chain,
    )
else:
    from config.dd_checklist import NO_RECORD_VALUE
    from service.due_diligence_case import (
        DueDiligenceCase,
        EnterpriseClaim,
        SourceChannel,
        SourceDescriptor,
        SourceOutcome,
        SourceQueryResult,
        validate_loaded_due_diligence_case,
    )
    from service.verification import (
        ReplayReport,
        parse_iso,
        record_structured_evidence,
        register_adapter,
        verify_evidence_chain,
    )


ADAPTER_ID = "canonical_factoring_case_v1"

# This is intentionally a closed set.  A new claim field is visible as an
# unverified claim until a deterministic normalizer and source policy are added
# here; arbitrary case data may never obtain a structured-evidence identity.
CANONICAL_FIELD_POLICY = frozenset({
    "entity_registration_status",
    "receivable_face_amount",
    "delivery_and_acceptance",
    "debtor_confirmation",
    "prior_transfer_or_pledge",
    "undisclosed_financing",
    "movable_mortgage",
    "site_operations",
    "legal_credit_record",
})


# The verification registry is a closed gate.  This adapter has no profile
# patch projector because canonical transaction checks must not mutate the
# enterprise risk-scoring profile.
register_adapter(
    ADAPTER_ID,
    "已验证的虚构保理案例：确定性聚合查询结果到 claim-scoped 结构化证据",
)


@dataclass
class CaseEvidenceBridge:
    """The isolated structured-evidence view of one canonical case."""

    case_id: str
    cutoff_date: str
    field_checks: List[Dict[str, Any]]
    evidence_store: Dict[str, Dict[str, Any]]


def claim_runtime_field_id(case: DueDiligenceCase, claim: EnterpriseClaim) -> str:
    """Return a per-claim evidence key; canonical ``field_id`` alone is unsafe.

    Query results are scoped by ``(subject_entity_id, field_id)`` while the
    legacy evidence chain historically binds only one field string.  Embedding
    the case, claim, and subject into that string prevents a source for one
    entity from being borrowed by an equally named claim on another entity.
    """

    return (
        f"case.{case.manifest.case_id}.claim.{claim.claim_id}."
        f"subject.{claim.subject_entity_id}"
    )


def _json_safe(model: Any) -> Any:
    """Make immutable Pydantic records safe for evidence-store deepcopy/replay."""

    return json.loads(model.model_dump_json())


def _claim_value(claim: EnterpriseClaim) -> Any:
    if claim.assertion is not None:
        return claim.assertion
    if claim.measurement is None:  # The case contract forbids this shape.
        return None
    return _json_safe(claim.measurement)


def _base_check(case: DueDiligenceCase, claim: EnterpriseClaim) -> Dict[str, Any]:
    """Make a claim visible without turning its assertion into a verified value."""

    return {
        "field_id": claim_runtime_field_id(case, claim),
        "field_name": claim.field_id,
        "canonical_field_id": claim.field_id,
        "claim_id": claim.claim_id,
        "subject_entity_id": claim.subject_entity_id,
        "claimed_value": _claim_value(claim),
        "asserted_by": claim.asserted_by,
        "claim_as_of_date": claim.as_of_date.isoformat(),
        "supporting_material_ids": list(claim.supporting_material_ids),
        "scope": "canonical:factoring",
        "required": False,
        "status": "unverified",
        "value": None,
        "sources": [],
        "attempted_sources": [],
        "source_outcomes": [],
        "failure_reason": "申请主张尚未获得独立结构化核验",
        "conflict_detail": [],
        "evidence_ids": [],
    }


def _text(value: Any) -> str:
    return "".join(str(value or "").lower().split()).replace("-", "_")


def _decimal_text(value: Any) -> Optional[str]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    normalized = number.normalize()
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _cny(value: Any) -> Optional[str]:
    number = _decimal_text(value)
    return f"CNY:{number}" if number is not None else None


def _claim_cny(claim: EnterpriseClaim) -> Optional[str]:
    measurement = claim.measurement
    if measurement is None:
        return None
    factors = {"CNY": Decimal("1"), "CNY_10K": Decimal("10000"), "CNY_MILLION": Decimal("1000000")}
    factor = factors.get(measurement.unit.value)
    if factor is None:
        return None
    return _cny(measurement.value * factor)


def _one_cny(record: Mapping[str, Any], keys: Sequence[str]) -> Optional[str]:
    values = {_cny(record.get(key)) for key in keys if record.get(key) not in (None, "")}
    values.discard(None)
    return next(iter(values)) if len(values) == 1 else None


def _active(value: Any) -> Optional[str]:
    value = _text(value)
    if value in {"active", "normal", "operating", "存续", "在业", "正常"}:
        return "active"
    if value in {"cancelled", "canceled", "revoked", "注销", "吊销", "inactive"}:
        return "inactive"
    return None


def _none_or_present(value: Any) -> Optional[str]:
    text = _text(value)
    if text in {"none", "no", "false", "0", "no_prior_transfer_or_pledge", "no_undisclosed_financing", "no_movable_mortgage"}:
        return "none"
    if text in {"present", "yes", "true", "active"}:
        return "present"
    return None


def _delivery(value: Any) -> Optional[str]:
    text = _text(value)
    if any(token in text for token in ("partial", "部分", "reconciliation_only")):
        return "partial"
    if any(token in text for token in ("full", "fully", "全部", "complete")):
        return "full"
    return None


def _confirmation(value: Any) -> Optional[str]:
    text = _text(value)
    if text in {"none", "no_response", "未回复", "未响应"}:
        return "no_response"
    if any(token in text for token in ("partial", "部分", "reconciliation_only")):
        return "partial"
    if any(token in text for token in ("confirmed", "completed", "确认", "确权")):
        return "confirmed"
    return None


def _claim_semantic(claim: EnterpriseClaim) -> Optional[str]:
    """Normalize only the nine closed-policy claim shapes."""

    field_id = claim.field_id
    if field_id not in CANONICAL_FIELD_POLICY:
        return None
    if field_id == "receivable_face_amount":
        return _claim_cny(claim)

    assertion = claim.assertion
    if assertion is None:
        return None
    if field_id == "entity_registration_status":
        return _active(assertion)
    if field_id == "delivery_and_acceptance":
        return _delivery(assertion)
    if field_id == "debtor_confirmation":
        return _confirmation(assertion)
    if field_id in {"prior_transfer_or_pledge", "undisclosed_financing", "movable_mortgage"}:
        assertion_text = _text(assertion)
        if (
            any(token in assertion_text for token in ("不存在", "未转让", "未质押", "no_", "none"))
            or (
                field_id == "prior_transfer_or_pledge"
                and "未" in assertion_text
                and any(token in assertion_text for token in ("转让", "质押"))
            )
        ):
            return "none"
        return _none_or_present(assertion)
    if field_id == "site_operations":
        return _active(assertion)
    if field_id == "legal_credit_record":
        if any(token in _text(assertion) for token in ("不存在", "无", "no_", "none")):
            return "none"
    return None


def _record_semantic(field_id: str, record: Mapping[str, Any]) -> Optional[str]:
    """Closed, key-driven normalizers for the canonical field policy."""

    if field_id == "entity_registration_status":
        return _active(record.get("registration_state") or record.get("operating_status"))

    if field_id == "receivable_face_amount":
        return _one_cny(record, (
            "contract_amount_cny", "invoice_amount_cny", "claimed_receivable_amount_cny",
            "registered_receivable_amount_cny",
        ))

    if field_id == "delivery_and_acceptance":
        declared = _delivery(
            record.get("delivery_scope")
            or record.get("delivery_declaration")
            or record.get("confirmation_scope")
        )
        if declared is not None:
            return declared
        delivery = _cny(record.get("delivery_amount_cny"))
        accepted = _cny(record.get("accepted_amount_cny"))
        if delivery is not None and accepted is not None:
            return "full" if delivery == accepted else "partial"
        return None

    if field_id == "debtor_confirmation":
        declared = _confirmation(
            record.get("confirmation_scope")
            or record.get("confirmation_declaration")
            or record.get("response_by_cutoff")
        )
        if declared is not None:
            return declared
        if _cny(record.get("confirmed_payable_cny")) is not None:
            return "confirmed"
        return None

    if field_id == "prior_transfer_or_pledge":
        declared = _none_or_present(record.get("transfer_declaration"))
        if declared is not None:
            return declared
        amount = _one_cny(record, ("registered_receivable_amount_cny",))
        if amount is not None:
            return f"present:{amount}"
        if record.get("registration_number") or _text(record.get("registration_state")) == "active":
            return "present"
        return None

    if field_id == "undisclosed_financing":
        declared = _none_or_present(record.get("financing_declaration"))
        if declared is not None:
            return declared
        if "unrelated_financing_detected" in record:
            return "present" if record.get("unrelated_financing_detected") is True else "none"
        amount = _one_cny(record, (
            "material_financing_balance_cny", "outstanding_factoring_financing_cny",
            "inbound_financing_cny",
        ))
        if amount is None:
            return None
        return "none" if amount == "CNY:0" else f"present:{amount}"

    if field_id == "movable_mortgage":
        declared = _none_or_present(record.get("mortgage_declaration"))
        if declared is not None:
            return declared
        amount = _one_cny(record, ("secured_debt_principal_cny",))
        if amount is not None:
            return "none" if amount == "CNY:0" else f"present:{amount}"
        if record.get("registration_number"):
            return "present"
        return None

    if field_id == "site_operations":
        if "operations_observed" in record:
            return "active" if record.get("operations_observed") is True else "inactive"
        return _active(record.get("operating_status") or record.get("site_status"))

    if field_id == "legal_credit_record":
        counts = [record.get(key) for key in ("litigation_count", "enforcement_count", "dishonesty_count")
                  if record.get(key) not in (None, "")]
        if counts:
            numbers = [_decimal_text(value) for value in counts]
            if any(number is None for number in numbers):
                return None
            return "none" if all(number == "0" for number in numbers) else "present"
        if record.get("case_no") or record.get("record_id"):
            return "present"
    return None


def _result_fact_date(result: SourceQueryResult) -> Optional[str]:
    values = [value for value in (
        result.as_of_date, result.publication_date, result.observed_at,
    ) if value is not None]
    return max(values).isoformat() if values else None


def _issuer_role(case: DueDiligenceCase, source: SourceDescriptor) -> str:
    """Classify whether an enterprise-submitted record is independently signed."""

    if source.source_type == "loan_application_declaration":
        return "assertion"
    if source.source_channel in {SourceChannel.PUBLIC, SourceChannel.AUTHORIZED, SourceChannel.SITE_VISIT}:
        return "independent"
    if source.source_channel is not SourceChannel.ENTERPRISE_SUBMITTED:
        return "unknown"

    # Display text is not an identity proof: a malicious package can copy the
    # debtor's name into ``issuer``.  The loader validates issuer_entity_id's
    # existence and name equality; absence is deliberately not inferred.
    issuer_entity_id = getattr(source, "issuer_entity_id", None)
    if not issuer_entity_id:
        return "unknown"
    entity_by_id = {entity.entity_id: entity for entity in case.entities}
    issuer_entity = entity_by_id.get(issuer_entity_id)
    if issuer_entity is None or issuer_entity.name != source.issuer:
        return "unknown"
    if issuer_entity.entity_id == case.loan_application.applicant_supplier_id:
        return "assertion"
    return "independent"


def _eligible_no_record(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
    source: SourceDescriptor,
    result: SourceQueryResult,
) -> bool:
    return (
        result.outcome is SourceOutcome.SUCCESS_NO_RECORD
        and claim.field_id == "prior_transfer_or_pledge"
        and source.source_type == "accounts_receivable_registry"
        and source.source_channel in {SourceChannel.PUBLIC, SourceChannel.AUTHORIZED}
        and _scope_has_exact_contract(source.query_scope, case.loan_application.contract_number)
    )


_TRANSACTION_BOUND_FIELDS = frozenset({
    "receivable_face_amount",
    "delivery_and_acceptance",
    "debtor_confirmation",
    "prior_transfer_or_pledge",
})


def _scope_has_exact_contract(query_scope: str, contract_number: str) -> bool:
    """Recognize the complete contract token, never an arbitrary substring.

    The v1 case contract has only narrative ``query_scope`` for no-record
    receipts.  This deliberately narrow simulation policy is not a substitute
    for a future structured query receipt, so malformed/ambiguous scope fails
    closed here.
    """

    boundary = r"A-Za-z0-9_.-"
    token = re.escape(contract_number)
    return re.search(rf"(?<![{boundary}]){token}(?![{boundary}])", query_scope) is not None


def _record_belongs_to_application(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
    source: SourceDescriptor,
    record: Mapping[str, Any],
) -> bool:
    """Bind successful records to this application before normalization."""

    if source.source_type == "loan_application_declaration":
        if record.get("application_id") != case.loan_application.application_id:
            return False
    if claim.field_id in _TRANSACTION_BOUND_FIELDS:
        contract_number = record.get("contract_number") or record.get("underlying_contract_number")
        if contract_number != case.loan_application.contract_number:
            return False
    return True


def _outcome_entry(source: SourceDescriptor, result: SourceQueryResult, role: str) -> Dict[str, Any]:
    return {
        "source_id": source.source_id,
        "source_type": source.source_type,
        "source_channel": source.source_channel.value,
        "issuer": source.issuer,
        "outcome": result.outcome.value,
        "outcome_detail": result.outcome_detail or "",
        "retrieved_at": result.retrieved_at.isoformat() if result.retrieved_at else "",
        "fact_date": _result_fact_date(result) or "",
        "independence": role,
        "record_count": len(result.records),
    }


def _success_observation(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
    source: SourceDescriptor,
    result: SourceQueryResult,
    role: str,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Return one source observation, or an explicit reason for not using it."""

    if role == "unknown":
        return None, "enterprise_submitted 的 issuer 未精确匹配到非申请人实体，拒绝作为独立来源"
    if result.outcome is SourceOutcome.SUCCESS_NO_RECORD:
        if not _eligible_no_record(case, claim, source, result):
            return None, "success_no_record 不满足 prior_transfer_or_pledge 的精确范围规则"
        return {
            "source": source.source_id,
            "value": "none",
            "display_value": NO_RECORD_VALUE,
            "role": "independent",
            "no_record": True,
            "retrieved_at": result.retrieved_at.isoformat(),
            "fact_date": _result_fact_date(result) or "",
        }, ""

    if result.outcome is not SourceOutcome.SUCCESS_WITH_RECORDS:
        return None, ""

    # ``SourceQueryResult.records`` is deliberately immutable.  Make a JSON
    # copy before extracting so neither normalization nor the later evidence
    # payload can accidentally retain a MappingProxyType from the loader.
    records = _json_safe(result)["records"]
    bound_records = [
        record for record in records
        if _record_belongs_to_application(case, claim, source, record)
    ]
    if not bound_records:
        return None, "成功查询 records 未绑定到本申请的 application_id/contract_number"
    values = {
        value for value in (_record_semantic(claim.field_id, record) for record in bound_records)
        if value is not None
    }
    if len(values) != 1:
        return None, (
            "成功查询的 records 未能确定性映射为单一字段取值"
            if not values else "同一来源 records 给出多个字段取值，拒绝自动采信"
        )
    value = next(iter(values))
    return {
        "source": source.source_id,
        "value": value,
        "display_value": value,
        "role": role,
        "no_record": False,
        "retrieved_at": result.retrieved_at.isoformat(),
        "fact_date": _result_fact_date(result) or "",
    }, ""


def _relevant_pairs(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
) -> Iterable[tuple[SourceDescriptor, SourceQueryResult]]:
    source_by_id = {source.source_id: source for source in case.sources}
    for result in case.query_results:
        source = source_by_id[result.source_id]
        if (
            source.subject_entity_id == claim.subject_entity_id
            and claim.field_id in result.queried_field_ids
        ):
            yield source, result


def _failure_reason(
    claim: EnterpriseClaim,
    observations: Sequence[Dict[str, Any]],
    notes: Sequence[str],
) -> str:
    if claim.field_id not in CANONICAL_FIELD_POLICY:
        return "该 canonical field 未登记确定性结构化证据策略，保持未核实"
    if not [item for item in observations if item["role"] == "independent"]:
        assertion_seen = any(item["role"] == "assertion" for item in observations)
        prefix = "仅有申请人声明或申请人签发材料，不构成独立核实" if assertion_seen else "未取得独立成功查询结果"
    else:
        prefix = "独立来源未能形成可自动采信的单一结论"
    detail = "；".join(dict.fromkeys(note for note in notes if note))
    return f"{prefix}{'：' + detail if detail else ''}"


def _raw_payload(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
    check: Mapping[str, Any],
    pairs: Sequence[tuple[SourceDescriptor, SourceQueryResult]],
    observations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    return {
        "case_id": case.manifest.case_id,
        "runtime_field_id": check["field_id"],
        "canonical_field_id": claim.field_id,
        "subject_entity_id": claim.subject_entity_id,
        "query_results": [
            {
                "source_id": source.source_id,
                "query_scope": source.query_scope,
                "source": _json_safe(source),
                "result": _json_safe(result),
            }
            for source, result in pairs
        ],
        "normalized_observations": [dict(item) for item in observations],
    }


def _observation_timing(
    observations: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    """Return the latest retrieval/fact dates among observations we accepted.

    A successful response is retained in ``raw`` even when it is applicant-side,
    unbound, or cannot be normalized.  It must not, however, make the final
    evidence look fresher than the observations that actually established its
    verified or conflicting conclusion.
    """

    retrievals: List[tuple[Any, str]] = []
    for observation in observations:
        retrieved_at = observation.get("retrieved_at")
        parsed = parse_iso(retrieved_at)
        if parsed is None:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        retrievals.append((parsed, str(retrieved_at)))
    if not retrievals:
        raise ValueError("采信的来源观察缺少合法 retrieved_at")

    fact_dates = [
        str(observation["fact_date"])
        for observation in observations
        if observation.get("fact_date")
    ]
    return max(retrievals, key=lambda item: item[0])[1], max(fact_dates, default="")


def _resolve_claim(
    case: DueDiligenceCase,
    claim: EnterpriseClaim,
    check: Dict[str, Any],
    evidence_store: Dict[str, Dict[str, Any]],
) -> None:
    pairs = list(_relevant_pairs(case, claim))
    observations: List[Dict[str, Any]] = []
    notes: List[str] = []
    successful_pairs: List[tuple[SourceDescriptor, SourceQueryResult]] = []

    claim_value = _claim_semantic(claim)
    if claim_value is not None:
        observations.append({
            "source": f"claim:{claim.claim_id}",
            "value": claim_value,
            "display_value": claim_value,
            "role": "assertion",
            "no_record": False,
            "retrieved_at": "",
        })

    for source, result in pairs:
        role = _issuer_role(case, source)
        entry = _outcome_entry(source, result, role)
        check["source_outcomes"].append(entry)
        if result.is_success:
            check["attempted_sources"].append(source.source_id)
            successful_pairs.append((source, result))
            observation, note = _success_observation(case, claim, source, result, role)
            if observation is not None:
                observations.append(observation)
            if note:
                notes.append(f"{source.source_id}: {note}")
        elif result.outcome_detail:
            notes.append(f"{source.source_id}: {result.outcome_detail}")

    independent = [item for item in observations if item["role"] == "independent"]
    # Only the canonical claim and independent observations may form a final
    # conflict.  Applicant-side source material remains in ``raw`` for audit,
    # but cannot manufacture a conflict or refresh its evidence timestamp.
    conflict_observations = [
        item for item in observations
        if item["role"] == "independent" or item["source"].startswith("claim:")
    ]
    values = {item["value"] for item in conflict_observations}
    independent_values = {item["value"] for item in independent}
    raw = _raw_payload(case, claim, check, successful_pairs, observations)

    # A conflict may arise between an application assertion and independent
    # evidence, or solely among independent sources.  The writer validates the
    # two-source/two-value invariant again at the atomic commit point.
    if independent and len(values) > 1:
        conflict_values = [
            {"source": item["source"], "value": item["display_value"],
             "retrieved_at": item["retrieved_at"]}
            for item in conflict_observations
        ]
        retrieved_at, as_of_date = _observation_timing(conflict_observations)
        record_structured_evidence(
            evidence_store,
            check,
            source_adapter=ADAPTER_ID,
            status="conflicting",
            conflict_values=conflict_values,
            raw=raw,
            retrieved_at=retrieved_at,
            as_of_date=as_of_date,
            failure_reason="申请主张与独立来源不一致，或独立来源之间存在不一致",
        )
        return

    # A verified conclusion requires a deterministic independent observation.
    # Success itself, applicant-side material, and an unrecognized record are
    # deliberately insufficient.
    if len(independent_values) == 1:
        semantic = next(iter(independent_values))
        display = next(
            item["display_value"] for item in independent
            if item["value"] == semantic
        )
        accepted_independent = [
            item for item in independent if item["value"] == semantic
        ]
        retrieved_at, as_of_date = _observation_timing(accepted_independent)
        record_structured_evidence(
            evidence_store,
            check,
            source_adapter=ADAPTER_ID,
            status="verified",
            value=display,
            raw=raw,
            retrieved_at=retrieved_at,
            as_of_date=as_of_date,
        )
        return

    check["failure_reason"] = _failure_reason(claim, observations, notes)


def build_case_evidence(case: DueDiligenceCase) -> CaseEvidenceBridge:
    """Build claim-scoped checks and structured evidence from a validated case.

    The explicit runtime type check is intentional: paths, plain dictionaries,
    and arbitrary source payloads are not adapter inputs.  They must first pass
    ``load_due_diligence_case`` and its cross-file validation contract.
    """

    if not isinstance(case, DueDiligenceCase):
        raise TypeError("case evidence adapter requires a validated DueDiligenceCase")
    validate_loaded_due_diligence_case(case)

    checks = [_base_check(case, claim) for claim in case.claims]
    evidence_store: Dict[str, Dict[str, Any]] = {}
    for claim, check in zip(case.claims, checks):
        _resolve_claim(case, claim, check, evidence_store)
    return CaseEvidenceBridge(
        case_id=case.manifest.case_id,
        cutoff_date=case.manifest.cutoff_date.isoformat(),
        field_checks=checks,
        evidence_store=evidence_store,
    )


def verify_case_evidence(bridge: CaseEvidenceBridge) -> ReplayReport:
    """Replay the bridge using the canonical evidence boundary, not run time."""

    return verify_evidence_chain(
        {}, bridge.field_checks, bridge.evidence_store, as_of=bridge.cutoff_date
    )


__all__ = [
    "ADAPTER_ID",
    "CANONICAL_FIELD_POLICY",
    "CaseEvidenceBridge",
    "build_case_evidence",
    "claim_runtime_field_id",
    "verify_case_evidence",
]
