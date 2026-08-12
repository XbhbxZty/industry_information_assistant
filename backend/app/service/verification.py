"""
核实来源与结构化证据链（v0.6）

## 要解决的问题

`verified_profile_mismatches()` 此前假设**所有** verified 字段都必须由初始
`company_profile` 重放得出。当前成立，只因 Scout 至今只追加 `attempted_sources`
而从不翻转状态。一旦结构化适配器查到新字段并置为 verified，初始档案无法重放
该结果，合法增量证据会被误判为状态不一致，导致全面 fail-closed。

修法不是放宽校验，而是**让每条核实都自带可审计的来源与证据**，
再按来源选择重放依据。

## 三条不可让步的规则

1. **`attempted_sources` 不是证据。** 它只说明"尝试过"，
   与"查到了什么"无关。仅凭它不得升级为 verified。
2. **通用网页检索不是结构化核实来源。** 自然语言事实无法承担
   字段级核实——它没有稳定的字段映射，也无法在重放时比对取值。
   `WEB_SEARCH` 因此**不在**合法来源集合内，出现即 fail-closed。
3. **来源不明时不得静默猜测。** 旧检查点缺少来源标记是既成事实，
   但降级路径必须显式记录并向上暴露，不能悄悄按"应该是档案来的"处理。

## 为什么 conflicting 也要证据

冲突状态会直接抬高风险等级并强制人工复核。若冲突取值无法追溯到具体来源，
复核人无从判断该信谁——一个没有出处的"冲突"和编造的冲突无法区分。
因此 conflicting 与 verified 同样要求完整证据链。

## 本轮边界（v0.6 只建模型，不接真实数据源）

- 不实现任何真实外部适配器
- 不让 Scout 真正把字段升级为 verified
- 只建立来源模型、证据结构、重放分发与行为断言
"""
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict
import uuid


# ---------------------------------------------------------------- 来源模型

# 初始企业档案（v0.1 起的硬编码档案；v0.4 后由 mock 数据源提供）
ORIGIN_INITIAL_PROFILE = "initial_profile"

# 结构化数据源适配器（工商/司法/招投标…）。返回结构化字段而非自然语言。
ORIGIN_STRUCTURED_ADAPTER = "structured_adapter"

# 合法来源的**闭集**。不在其中的一律 fail-closed，包括：
#   - 通用网页检索（自然语言事实，无稳定字段映射）
#   - LLM 推断
#   - 空值 / 拼写错误
VALID_ORIGINS = frozenset({ORIGIN_INITIAL_PROFILE, ORIGIN_STRUCTURED_ADAPTER})

# 旧检查点没有来源标记。**不当作合法来源**，而是单独识别出来走降级路径，
# 使"这是历史数据"这件事在结果里可见，而不是被悄悄当成 initial_profile。
ORIGIN_LEGACY_UNKNOWN = "legacy_unknown"

# 需要完整证据链的状态。unverified / not_applicable 无需证据——
# 它们本就不主张任何事实。
EVIDENCE_REQUIRED_STATUSES = frozenset({"verified", "conflicting"})


class StructuredEvidence(TypedDict, total=False):
    """
    结构化适配器产出的单条证据。

    与 `facts` 的区别：facts 是给 LLM 读的自然语言，evidence 是给**程序重放**用的
    结构化记录。两者不可互相替代——自然语言无法在重放时做等值比对。
    """
    evidence_id: str
    field_id: str
    source_adapter: str        # 产出该证据的适配器标识
    retrieved_at: str          # ISO 时间戳，缺失即 fail-closed
    value: Any                 # verified 时的结构化取值
    conflict_values: List[Dict[str, Any]]   # conflicting 时各来源取值
    raw: Dict[str, Any]        # 适配器原始返回，供人工追溯


# ---------------------------------------------------------------- 失败原因

REASON_MISSING_ORIGIN = "missing_origin"
REASON_INVALID_ORIGIN = "invalid_origin"
REASON_MISSING_TIMESTAMP = "missing_retrieved_at"
REASON_NO_EVIDENCE_IDS = "no_evidence_ids"
REASON_EVIDENCE_NOT_FOUND = "evidence_not_found"
REASON_EVIDENCE_VALUE_MISMATCH = "evidence_value_mismatch"
REASON_PROFILE_REPLAY_MISMATCH = "profile_replay_mismatch"
REASON_MISSING_CONFLICT_VALUES = "missing_conflict_values"


def _fail(field_check: Dict, reason: str, detail: str, **extra) -> Dict[str, Any]:
    return {
        "field_id": field_check.get("field_id"),
        "field_name": field_check.get("field_name", field_check.get("field_id", "")),
        "status": field_check.get("status"),
        "verification_origin": field_check.get("verification_origin"),
        "reason": reason,
        "detail": detail,
        **extra,
    }


# ---------------------------------------------------------------- 证据写入

def new_evidence_id(field_id: str) -> str:
    return f"ev_{field_id}_{uuid.uuid4().hex[:8]}"


def record_structured_evidence(
    evidence_store: Dict[str, Dict],
    field_check: Dict,
    *,
    source_adapter: str,
    value: Any = None,
    conflict_values: Optional[List[Dict[str, Any]]] = None,
    raw: Optional[Dict[str, Any]] = None,
    retrieved_at: Optional[str] = None,
) -> str:
    """
    结构化适配器核实字段时的**唯一**写入口。

    要求 5：适配器不得只改 `field_checks` 的状态，必须同步落一条结构化证据。
    把"改状态"与"写证据"绑在同一个函数里，是为了让"只改状态"这条路径
    在代码层就走不通——分成两步就总会有人只做第一步。

    同时写入 check 的来源字段，保证状态与证据不会各自漂移。
    """
    ev_id = new_evidence_id(field_check["field_id"])
    ts = retrieved_at or datetime.now().isoformat()
    evidence_store[ev_id] = {
        "evidence_id": ev_id,
        "field_id": field_check["field_id"],
        "source_adapter": source_adapter,
        "retrieved_at": ts,
        "value": value,
        "conflict_values": conflict_values or [],
        "raw": raw or {},
    }
    field_check["verification_origin"] = ORIGIN_STRUCTURED_ADAPTER
    field_check["source_adapter"] = source_adapter
    field_check["retrieved_at"] = ts
    field_check.setdefault("evidence_ids", []).append(ev_id)
    return ev_id


def stamp_initial_profile_origin(field_checks: List[Dict], retrieved_at: str) -> None:
    """
    给由初始档案填充的清单打上来源标记。

    在 `fill_field_checks()` 之后调用。只标记需要证据的状态——
    unverified 不主张任何事实，标了反而会让"有来源"失去含义。
    """
    for c in field_checks:
        if c.get("status") in EVIDENCE_REQUIRED_STATUSES:
            c["verification_origin"] = ORIGIN_INITIAL_PROFILE
            c["source_adapter"] = ORIGIN_INITIAL_PROFILE
            c["retrieved_at"] = retrieved_at
            c.setdefault("evidence_ids", [])


# ---------------------------------------------------------------- 重放校验

class ReplayReport:
    """
    重放结果。

    `mismatches` 必须触发 fail-closed；`degradations` 是需要显式披露、
    但不必然阻断的情况（当前只有旧检查点缺来源标记一种）。
    分开两个列表，是为了让"证据链断了"与"这是历史数据"在下游可区分——
    合成一个列表会逼调用方靠字符串匹配去猜。
    """

    def __init__(self) -> None:
        self.mismatches: List[Dict[str, Any]] = []
        self.degradations: List[Dict[str, Any]] = []

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> Dict[str, Any]:
        return {"mismatches": self.mismatches, "degradations": self.degradations}


def _check_common_provenance(check: Dict) -> Optional[Dict[str, Any]]:
    """来源与时间戳的通用校验；返回失败记录或 None"""
    origin = check.get("verification_origin")
    if not origin:
        return None   # 交由调用方按 legacy 处理
    if origin not in VALID_ORIGINS:
        return _fail(
            check, REASON_INVALID_ORIGIN,
            f"来源 {origin!r} 不在合法集合 {sorted(VALID_ORIGINS)} 内。"
            f"通用网页检索与模型推断不构成字段级核实证据",
        )
    if not check.get("retrieved_at"):
        return _fail(
            check, REASON_MISSING_TIMESTAMP,
            "缺少 retrieved_at；无法判断证据时效，不予采信",
        )
    return None


def _replay_structured(check: Dict, evidence_store: Dict[str, Dict]) -> Optional[Dict[str, Any]]:
    """按结构化证据重放：证据必须存在，且取值与清单一致"""
    ev_ids = check.get("evidence_ids") or []
    if not ev_ids:
        return _fail(
            check, REASON_NO_EVIDENCE_IDS,
            "声称来自结构化适配器却没有 evidence_ids。"
            "attempted_sources 只表示尝试过，不构成核实证据",
            attempted_sources=check.get("attempted_sources") or [],
        )

    found = [evidence_store.get(e) for e in ev_ids]
    missing = [e for e, ev in zip(ev_ids, found) if ev is None]
    if missing:
        return _fail(
            check, REASON_EVIDENCE_NOT_FOUND,
            f"evidence_id 在证据库中不存在：{missing}",
            missing_evidence_ids=missing,
        )

    if check.get("status") == "conflicting":
        # 冲突必须能列出各来源取值，否则复核人无从判断该信谁
        all_conflicts = [cv for ev in found for cv in (ev.get("conflict_values") or [])]
        if len(all_conflicts) < 2:
            return _fail(
                check, REASON_MISSING_CONFLICT_VALUES,
                f"冲突状态但证据中的来源取值不足 2 条（实际 {len(all_conflicts)} 条），"
                f"无法还原分歧内容",
            )
        return None

    # verified：证据取值必须与清单取值一致
    values = [ev.get("value") for ev in found]
    if check.get("value") not in values:
        return _fail(
            check, REASON_EVIDENCE_VALUE_MISMATCH,
            "清单取值无法在任一条证据中找到，两者已漂移",
            check_value=check.get("value"),
            evidence_values=values,
        )
    return None


def verify_evidence_chain(
    company: Dict[str, Any],
    field_checks: List[Dict],
    evidence_store: Optional[Dict[str, Dict]] = None,
    *,
    profile_replay_fn=None,
    allow_legacy_profile_replay: bool = True,
) -> ReplayReport:
    """
    按 `verification_origin` 分发重放依据，校验证据链完整性。

    - `initial_profile`    → 从 company_profile 重放（复用 fill_field_checks）
    - `structured_adapter` → 从 evidence_store 重放
    - 缺来源（旧检查点）    → 走 legacy 路径，**显式记录降级**

    Args:
        allow_legacy_profile_replay:
            旧检查点缺少来源标记时，是否允许按档案重放兜底。
            默认 True 以兼容既有检查点；为 False 时一律 fail-closed。
            无论取值如何，该情况**始终**进入 degradations，不会静默通过。
    """
    evidence_store = evidence_store or {}
    report = ReplayReport()

    need_profile_replay: List[Dict] = []
    legacy_checks: List[Dict] = []

    for check in field_checks:
        if check.get("status") not in EVIDENCE_REQUIRED_STATUSES:
            continue

        origin = check.get("verification_origin")
        if not origin:
            legacy_checks.append(check)
            continue

        problem = _check_common_provenance(check)
        if problem:
            report.mismatches.append(problem)
            continue

        if origin == ORIGIN_STRUCTURED_ADAPTER:
            problem = _replay_structured(check, evidence_store)
            if problem:
                report.mismatches.append(problem)
        else:
            need_profile_replay.append(check)

    # —— 旧检查点：显式降级，不静默猜测 ——
    for check in legacy_checks:
        report.degradations.append(_fail(
            check, REASON_MISSING_ORIGIN,
            "旧检查点缺少 verification_origin。"
            + ("已按初始档案重放兜底，结论需人工确认"
               if allow_legacy_profile_replay else "不予采信"),
            legacy_replay_allowed=allow_legacy_profile_replay,
        ))
        if allow_legacy_profile_replay:
            need_profile_replay.append(check)
        else:
            report.mismatches.append(_fail(
                check, REASON_MISSING_ORIGIN,
                "旧检查点缺少来源标记且已禁用兜底重放",
            ))

    # —— 档案重放：一次性批量比对 ——
    if need_profile_replay and profile_replay_fn is not None:
        predicted = profile_replay_fn(company, field_checks)
        for check in need_profile_replay:
            fid = check.get("field_id")
            exp = predicted.get(fid)
            if exp is None or exp.get("status") != check.get("status"):
                report.mismatches.append(_fail(
                    check, REASON_PROFILE_REPLAY_MISMATCH,
                    "初始档案无法重放出该状态（档案字段可能已缺失）",
                    profile_status=(exp or {}).get("status", "missing"),
                ))
                continue
            if check.get("status") == "verified" and exp.get("value") != check.get("value"):
                report.mismatches.append(_fail(
                    check, REASON_PROFILE_REPLAY_MISMATCH,
                    "初始档案重放出的取值与清单不一致",
                    check_value=check.get("value"),
                    profile_value=exp.get("value"),
                ))
    elif need_profile_replay:
        for check in need_profile_replay:
            report.mismatches.append(_fail(
                check, REASON_PROFILE_REPLAY_MISMATCH,
                "需要档案重放但未提供重放函数",
            ))

    return report
