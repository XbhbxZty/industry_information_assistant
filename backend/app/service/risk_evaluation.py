"""Shared, deterministic risk evaluation for production and offline evaluation.

This module has no Agent, state-machine, SSE, database or model dependency.
Evidence validation, scoring-view construction and credit advice intentionally
retain the production policy; callers only persist/present this result.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from config.dd_checklist import compute_completeness
    from config.verification_policy import POLICY
    from service.company_profile import replay_from_profile, verify_field_checks
    from service.credit_advice import recommend_credit
    from service.risk_scorecard import (
        PROFILE_BACKED_FIELDS, apply_provenance_gate, score as score_risk, unratable,
    )
    from service.verification import build_scoring_view
except ImportError:  # compatible with app as the package root
    from app.config.dd_checklist import compute_completeness
    from app.config.verification_policy import POLICY
    from app.service.company_profile import replay_from_profile, verify_field_checks
    from app.service.credit_advice import recommend_credit
    from app.service.risk_scorecard import (
        PROFILE_BACKED_FIELDS, apply_provenance_gate, score as score_risk, unratable,
    )
    from app.service.verification import build_scoring_view


@dataclass
class RiskEvaluation:
    assessment: Optional[Dict[str, Any]] = None
    completeness: Dict[str, Any] = field(default_factory=dict)
    # Never expose an incomplete/failed view for subsequent human overrides.
    scoring_view: Optional[Dict[str, Any]] = None
    diagnostics: Dict[str, Any] = field(default_factory=lambda: {
        "status": "skipped", "stage": "checklist", "chain_ok": False,
        "mismatches": [], "degradations": [], "unmergeable": [],
    })
    errors: List[str] = field(default_factory=list)


def evaluate_risk(
    company_profile: Optional[Dict[str, Any]],
    field_checks: Optional[List[Dict[str, Any]]],
    evidence_store: Optional[Dict[str, Any]] = None,
    *,
    as_of: str = "",
) -> RiskEvaluation:
    """Evaluate supplied evidence without mutating a research state.

    ``chain_ok`` means evidence replay *and* scoring-view merge succeeded; it
    does not mean the company is ratable or credit advice can be issued. The
    status/stage and original diagnostic rows retain these distinct outcomes.
    Empty checklists remain non-due-diligence runs (no fabricated assessment).
    """
    out = RiskEvaluation()
    checks = field_checks or []
    if not checks:
        return out

    diagnostic = out.diagnostics
    diagnostic["status"] = "blocked"
    try:
        diagnostic["stage"] = "completeness"
        out.completeness = compute_completeness(checks)
        diagnostic["stage"] = "profile"
        profile = company_profile or {}
        if not profile:
            out.assessment = unratable(
                "结构化企业档案缺失，无法执行风险评分（清单状态无法映射到具体数值）",
                out.completeness,
            )
            out.errors.append("风险评分：company_profile 缺失，已按不可评级处理")
            return out

        diagnostic["stage"] = "verification"
        store = evidence_store or {}
        report = verify_field_checks(profile, checks, store, as_of=as_of or "")
        diagnostic["mismatches"] = list(report.mismatches)
        diagnostic["degradations"] = list(report.degradations)
        for degradation in report.degradations:
            out.errors.append(
                f"证据链降级：{degradation['field_id']} {degradation['detail']}"
            )
        if report.mismatches:
            fields = "、".join(
                f"{m['field_id']}({m['reason']})" for m in report.mismatches
            )
            out.assessment = unratable(
                f"核查清单证据链不完整或与来源不一致（{fields}），不予评级",
                out.completeness,
            )
            out.errors.append(f"风险评分：证据链校验失败（{fields}），已按不可评级处理")
            return out

        diagnostic["stage"] = "scoring_view"
        view, unmergeable = build_scoring_view(
            profile, checks, store,
            profile_backed_fields=PROFILE_BACKED_FIELDS,
            profile_replay_fn=replay_from_profile,
        )
        diagnostic["unmergeable"] = list(unmergeable)
        diagnostic["chain_ok"] = not unmergeable
        if unmergeable:
            fields = "、".join(m["field_id"] for m in unmergeable)
            result = unratable(
                f"结构化证据无法并入评分数据视图（{fields}），"
                "评分卡会读到旧档案并可能得出相反结论，不予评级",
                out.completeness,
            )
            out.errors.append(f"风险评分：证据未提供 profile_patch（{fields}），已按不可评级处理")
        else:
            diagnostic["stage"] = "scoring"
            result = score_risk(view, checks, out.completeness)

        # Keep the production ordering: provenance gates precede credit advice,
        # whose financial inputs must come from the same merged scoring view.
        diagnostic["stage"] = "provenance"
        result = apply_provenance_gate(result, report.degradations, POLICY.degraded_level_floor)
        diagnostic["stage"] = "credit_advice"
        result["credit_recommendation"] = recommend_credit(view, checks, result)
        out.assessment = result
        if not unmergeable:
            out.scoring_view = view
            diagnostic.update(status="evaluated", stage="complete")
        else:
            diagnostic["stage"] = "scoring_view"
    except Exception as exc:
        diagnostic["status"] = "error"
        diagnostic["exception"] = {"type": type(exc).__name__, "message": str(exc)}
        out.assessment = unratable(
            f"风险评分执行失败（{type(exc).__name__}: {exc}），不予评级",
            out.completeness,
        )
        out.errors.append(f"风险评分执行失败: {exc}")
        out.scoring_view = None
    return out
