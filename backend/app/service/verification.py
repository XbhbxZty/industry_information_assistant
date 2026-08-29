# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
核实来源与结构化证据链（v0.6）

## 要解决的问题

`verified_profile_mismatches()` 此前假设**所有** verified 字段都必须由初始
`company_profile` 重放得出。当前成立，只因 Scout 至今只追加 `attempted_sources`
而从不翻转状态。一旦结构化适配器查到新字段并置为 verified，初始档案无法重放
该结果，合法增量证据会被误判为状态不一致，导致全面 fail-closed。

修法不是放宽校验，而是**让每条核实都自带可审计的来源与证据**，
再按来源选择重放依据。

## 四条不可让步的规则

1. **`attempted_sources` 不是证据。** 它只说明"尝试过"，
   与"查到了什么"无关。仅凭它不得升级为 verified。
2. **通用网页检索不是结构化核实来源。** 自然语言事实无法承担
   字段级核实——它没有稳定的字段映射，也无法在重放时比对取值。
   来源与适配器**都是闭集**，集合外一律 fail-closed。
3. **来源不明时不得静默猜测。** 旧检查点缺少来源标记是既成事实，
   但降级路径必须显式记录、向上暴露，并且**不得继续形成自动授信等级**。
4. **通过校验 ≠ 进入评分。** 证据必须能合并进评分卡真正读取的那份数据；
   合并不了就不予评级——否则新增的负面证据会被翻译成正面结论（BC-31）。

## 为什么 conflicting 也要证据

冲突状态会直接抬高风险等级并强制人工复核。若冲突取值无法追溯到具体来源，
复核人无从判断该信谁——一个没有出处的"冲突"和编造的冲突无法区分。
因此 conflicting 与 verified 同样要求完整证据链，且必须来自**不同来源的
不同取值**：两条同源同值凑不出分歧。

## 本轮边界（v0.6a 只建模型，不接真实数据源）

- 不实现任何真实外部适配器（`_TRUSTED_ADAPTERS` 因此初始为空）
- 不让 Scout 真正把字段升级为 verified
- 只建立来源模型、证据结构、重放分发、评分视图与行为断言
"""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict
import re
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
STATUS_VERIFIED = "verified"
STATUS_CONFLICTING = "conflicting"
EVIDENCE_REQUIRED_STATUSES = frozenset({STATUS_VERIFIED, STATUS_CONFLICTING})


# ------------------------------------------------------- 受信任适配器注册表

# 只有注册过的适配器才能产出 `structured_adapter` 身份。
#
# 为什么必须是注册表而不是"调用方传什么就是什么"：写入口一旦接受任意字符串，
# 调用方传 `source_adapter="web_search"` 就能让网页检索获得结构化核实身份——
# 规则 2 的闭集会被绕过。这不是假想：v0.6a 首版就有这个洞（BC-32）。
_TRUSTED_ADAPTERS: Dict[str, Dict[str, Any]] = {}


def register_adapter(
    adapter_id: str,
    description: str,
    *,
    project_profile_patch: Optional[Callable[[str, Dict[str, Any]], Optional[Dict[str, Any]]]] = None,
) -> None:
    """
    登记一个受信任的结构化数据源适配器。

    只有真正返回**结构化字段**、且能在重放时做等值比对的数据源才可登记。
    会影响评分档案的适配器必须同时登记 `project_profile_patch` 纯函数，
    由它从 raw 确定性生成 patch；调用方不能自由决定进入评分的数据。
    通用网页检索、LLM 抽取一律不得登记——它们产出的是自然语言。
    """
    if not adapter_id or not isinstance(adapter_id, str):
        raise ValueError("adapter_id 必须是非空字符串")
    _TRUSTED_ADAPTERS[adapter_id] = {
        "description": description,
        "project_profile_patch": project_profile_patch,
    }


def unregister_adapter(adapter_id: str) -> None:
    """仅供测试隔离使用。"""
    _TRUSTED_ADAPTERS.pop(adapter_id, None)


def is_trusted_adapter(adapter_id: str) -> bool:
    return adapter_id in _TRUSTED_ADAPTERS


def trusted_adapters() -> Dict[str, str]:
    return {k: v["description"] for k, v in _TRUSTED_ADAPTERS.items()}


def _project_registered_patch(
    adapter_id: str,
    field_id: str,
    raw: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    projector = (_TRUSTED_ADAPTERS.get(adapter_id) or {}).get("project_profile_patch")
    if projector is None:
        return None
    projected = projector(field_id, deepcopy(raw))
    if projected is not None and not isinstance(projected, dict):
        raise ValueError("适配器 profile patch 投影器必须返回 dict 或 None")
    return projected


class StructuredEvidence(TypedDict, total=False):
    """
    结构化适配器产出的单条证据。

    与 `facts` 的区别：facts 是给 LLM 读的自然语言，evidence 是给**程序重放**用的
    结构化记录。两者不可互相替代——自然语言无法在重放时做等值比对。

    `profile_patch` 是本轮补上的关键字段：证据不仅要能被校验，还必须能
    **合并进评分卡真正读取的那份结构化数据**。只有 `value`（展示用字符串）
    的证据通不过评分视图构建，会导致不予评级而不是被当成"无风险"。
    patch 必须由已注册适配器从 raw 确定性投影，而非调用方自由填写。
    """
    evidence_id: str
    field_id: str
    source_adapter: str        # 产出该证据的适配器标识，必须已注册
    retrieved_at: str          # ISO 时间戳，缺失或非法即 fail-closed
    as_of_date: str            # 该证据所述事实的发布/事件日期，用于研究截止日闸门
    value: Any                 # verified 时的结构化取值
    conflict_values: List[Dict[str, Any]]   # conflicting 时各来源取值
    profile_patch: Dict[str, Any]           # 可合并进评分数据视图的档案片段
    raw: Dict[str, Any]        # 适配器原始返回，供人工追溯
    active: bool               # 是否为字段当前结论使用的证据
    supersedes: List[str]      # 本证据替代了哪些旧证据
    supersede_reason: str      # 显式替代原因；首次写入为空
    superseded_by: str         # 被哪条新证据替代；仅历史证据存在


# ---------------------------------------------------------------- 失败原因

REASON_MISSING_ORIGIN = "missing_origin"
REASON_INVALID_ORIGIN = "invalid_origin"
REASON_MISSING_TIMESTAMP = "missing_retrieved_at"
REASON_NO_EVIDENCE_IDS = "no_evidence_ids"
REASON_EVIDENCE_NOT_FOUND = "evidence_not_found"
REASON_EVIDENCE_VALUE_MISMATCH = "evidence_value_mismatch"
REASON_PROFILE_REPLAY_MISMATCH = "profile_replay_mismatch"
REASON_MISSING_CONFLICT_VALUES = "missing_conflict_values"

# —— v0.6a 复核补充（BC-32）：证据与字段/适配器/时间/冲突状态的绑定 ——
REASON_UNTRUSTED_ADAPTER = "untrusted_adapter"
REASON_EVIDENCE_FIELD_MISMATCH = "evidence_field_mismatch"
REASON_EVIDENCE_ADAPTER_MISMATCH = "evidence_adapter_mismatch"
REASON_EVIDENCE_MISSING_TIMESTAMP = "evidence_missing_retrieved_at"
REASON_INVALID_TIMESTAMP = "invalid_retrieved_at"
REASON_TIMESTAMP_MISMATCH = "retrieved_at_mismatch"
REASON_EVIDENCE_MISSING_RAW = "evidence_missing_raw"
REASON_CONFLICT_SILENTLY_RESOLVED = "conflict_silently_resolved"
REASON_CONFLICT_SINGLE_SOURCE = "conflict_single_source"
REASON_CONFLICT_DETAIL_MISMATCH = "conflict_detail_mismatch"
REASON_VERIFIED_WITHOUT_VALUE = "verified_without_value"

# —— 评分视图（BC-31）——
REASON_EVIDENCE_NOT_MERGEABLE = "evidence_not_mergeable"
REASON_PATCH_FIELD_MISMATCH = "profile_patch_field_mismatch"
REASON_PATCH_VALUE_MISMATCH = "profile_patch_value_mismatch"
REASON_PATCH_RAW_MISMATCH = "profile_patch_raw_mismatch"
REASON_EVIDENCE_INACTIVE = "evidence_inactive"

# —— 研究截止日（P0-3）——
REASON_POST_CUTOFF_EVIDENCE = "post_cutoff_evidence"
REASON_AS_OF_DATE_UNKNOWN = "as_of_date_unknown"
REASON_INVALID_AS_OF_DATE = "invalid_as_of_date"
REASON_UNEXPECTED_PROFILE_SOURCES = "unexpected_profile_sources"


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


def parse_iso(ts: Any) -> Optional[datetime]:
    """宽松解析 ISO 日期/时间；不合法返回 None。接受 '2026-08-09' 与完整时间戳。"""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timestamp_key(ts: Any) -> Optional[datetime]:
    """把有/无时区的 ISO 时间统一成 UTC naive 值，供安全排序与比较。"""
    parsed = parse_iso(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------ 研究截止日闸门

def _end_of_day(d: datetime) -> datetime:
    """把只给到日的截止日按当日 23:59:59.999999 处理，避免误杀当天的证据。"""
    if (d.hour, d.minute, d.second, d.microsecond) == (0, 0, 0, 0):
        return d.replace(hour=23, minute=59, second=59, microsecond=999999)
    return d


def check_as_of(evidence: Dict[str, Any], as_of: str) -> Optional[Tuple[str, str]]:
    """
    判断一条证据相对研究截止日是否可用。

    ## retrieved_at 不能拿来做这个判断

    `retrieved_at` 是**我们什么时候取到的**，`as_of_date` 是**这条事实什么时候
    发布/发生的**。做回溯评测时，今天（2026-08）跑一个截止日为 2025-05-31 的
    案子，所有证据的 retrieved_at 都是今天——拿它比截止日会把每一条都判成越界，
    整个机制退化成"永远不给评级"。

    要卡的是另一件事：**2025-07 才披露的半年报，不得进入 2025-05-31 时点的判断**。
    这正是案例包 `as_of_eligible` 的语义，也是它把 S011/S012 单独放进
    `post_cutoff_outcomes` 而非 `sources` 的原因。

    ## 缺 as_of_date 时为什么不放行

    "不知道这条事实是什么时候的"和"知道它在截止日之前"是两件事。放行等于
    默认后者，而这正是回溯评测要防的信息泄漏。因此缺失记为**降级**：
    不阻断，但由 `apply_as_of_gate()` 剥夺自动低风险资格，交人工判断。

    Returns:
        None 表示可用；否则 (reason, detail)。
    """
    if not as_of:
        return None
    cutoff = _timestamp_key(as_of)
    if cutoff is None:
        return (REASON_INVALID_AS_OF_DATE,
                f"研究截止日 {as_of!r} 不是合法 ISO 日期，无法施加时点闸门")
    cutoff = _end_of_day(cutoff)

    raw_date = evidence.get("as_of_date")
    if not raw_date:
        return (REASON_AS_OF_DATE_UNKNOWN,
                f"证据未声明发布/事件日期，无法判断它是否晚于研究截止日 {as_of}")

    ev_date = _timestamp_key(raw_date)
    if ev_date is None:
        return (REASON_INVALID_AS_OF_DATE,
                f"证据的 as_of_date {raw_date!r} 不是合法 ISO 日期")
    if ev_date > cutoff:
        return (REASON_POST_CUTOFF_EVIDENCE,
                f"该证据的事实日期 {raw_date} 晚于研究截止日 {as_of}，"
                f"不得用于该时点的判断（可另作后验回测）")
    return None


# 字段证据只能修改评分卡消费的对应档案切片。共享容器进一步限制子字段，
# 避免“核实担保”时顺带注入一条虚假财务期（BC-36）。
_PATCH_TOP_LEVEL_KEY: Dict[str, str] = {
    "debt_ratio": "financials",
    "net_profit": "financials",
    "revenue": "financials",
    "cash_flow": "financials",
    "litigation": "judicial_records",
    "enforcement": "judicial_records",
    "dishonesty": "judicial_records",
    "guarantee": "guarantee",
    "guarantee_circle": "guarantee_circle",
    "operating_status": "registration",
    "bidding_record": "bidding_records",
    "negative_news": "negative_news",
    "regulatory_penalty": "regulatory_penalty",
}

_FINANCIAL_PATCH_VALUE_KEY = {
    "debt_ratio": "debt_ratio",
    "net_profit": "net_profit",
    "revenue": "revenue",
    "cash_flow": "operating_cash_flow",
}

_JUDICIAL_TYPE = {
    "litigation": "涉诉",
    "enforcement": "被执行",
    "dishonesty": "失信",
}


def _validate_profile_patch_shape(field_id: str, patch: Optional[Dict[str, Any]]) -> None:
    """拒绝跨字段 patch；只验证结构，内容一致性在评分前由生产映射重放。"""
    if not patch:
        return
    if not isinstance(patch, dict):
        raise ValueError("profile_patch 必须是字典")
    allowed_top = _PATCH_TOP_LEVEL_KEY.get(field_id)
    if allowed_top is None:
        raise ValueError(f"字段 {field_id!r} 未登记评分 patch 映射，不得提交 profile_patch")
    if set(patch) != {allowed_top}:
        raise ValueError(
            f"字段 {field_id!r} 的 profile_patch 只能修改 {allowed_top!r}，"
            f"实际包含 {sorted(patch)}"
        )

    payload = patch[allowed_top]
    if field_id in _FINANCIAL_PATCH_VALUE_KEY:
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"字段 {field_id!r} 的 financials patch 必须是非空列表")
        value_key = _FINANCIAL_PATCH_VALUE_KEY[field_id]
        allowed = {"period", value_key, "retrieved_at"}
        for row in payload:
            if not isinstance(row, dict) or value_key not in row or not row.get("period"):
                raise ValueError(f"字段 {field_id!r} 的每个财务期必须包含 period 与 {value_key}")
            if set(row) - allowed:
                raise ValueError(
                    f"字段 {field_id!r} 不得借 financials patch 修改 {sorted(set(row) - allowed)}"
                )
    elif field_id in _JUDICIAL_TYPE:
        if not isinstance(payload, list):
            raise ValueError(f"字段 {field_id!r} 的 judicial_records patch 必须是列表")
        wrong = [r.get("type") for r in payload
                 if not isinstance(r, dict) or r.get("type") != _JUDICIAL_TYPE[field_id]]
        if wrong:
            raise ValueError(
                f"字段 {field_id!r} 只能提交 type={_JUDICIAL_TYPE[field_id]!r} 的司法记录"
            )
    elif field_id == "operating_status":
        if not isinstance(payload, dict) or set(payload) != {"operating_status"}:
            raise ValueError("operating_status 只能修改 registration.operating_status")
    elif not isinstance(payload, list):
        raise ValueError(f"字段 {field_id!r} 的 {allowed_top} patch 必须是列表")


# ---------------------------------------------------------------- 证据写入

def new_evidence_id(field_id: str) -> str:
    return f"ev_{field_id}_{uuid.uuid4().hex[:8]}"


def _distinct_conflict_pairs(entries: List[Dict[str, Any]]) -> Tuple[set, set]:
    sources = {e.get("source") for e in entries if e.get("source")}
    values = {_hashable(e.get("value")) for e in entries if e.get("value") not in (None, "")}
    return sources, values


def _hashable(v: Any) -> Any:
    """把取值压成可放进 set 的形式；非标量一律用其 repr 比对。"""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)


def _normalized_display(v: Any) -> Any:
    """只消除结构化数值渲染中的无意义差异（1800 与 1800.0），不做语义猜测。"""
    if not isinstance(v, str):
        return v
    compact = re.sub(r"\s+", "", v)
    return re.sub(r"(?<=\d)\.0+(?=\D|$)", "", compact)


def record_structured_evidence(
    evidence_store: Dict[str, Dict],
    field_check: Dict,
    *,
    source_adapter: str,
    status: str,
    value: Any = None,
    conflict_values: Optional[List[Dict[str, Any]]] = None,
    profile_patch: Optional[Dict[str, Any]] = None,
    raw: Dict[str, Any],
    retrieved_at: str,
    as_of_date: str = "",
    failure_reason: str = "",
    supersedes_evidence_ids: Optional[List[str]] = None,
    supersede_reason: str = "",
) -> str:
    """
    结构化适配器核实字段时的**唯一原子写入口**。

    要求 5：适配器不得只改 `field_checks` 的状态，必须同步落一条结构化证据。
    本函数一次性写完 evidence_store 与 field_check 的
    status / value / conflict_detail / verification_origin / evidence_ids /
    source_adapter / retrieved_at ——**调用方不需要、也不应该自己动这些字段**。

    ⚠️ 这一点在 v0.6a 首版是假的：当时函数只写来源字段，状态和取值仍要调用方
    自己改，两步之间存在漂移窗口，而文档却宣称"绑定后那条路径走不通"。
    该缺陷记为 BC-34，本函数是修复后的版本。

    非法组合直接抛 `ValueError`：适配器传错参数是编程错误，应当当场炸掉，
    而不是写进证据库等重放时才发现。

    `profile_patch` 参数仅作为迁移期的可选一致性断言：若传入，必须等于注册
    投影器从 raw 生成的结果；真正写入 evidence_store 的始终是投影器产物。

    字段已有当前证据时，调用方还必须明确提交完整的
    `supersedes_evidence_ids` 与非空 `supersede_reason`；调用顺序不自动代表
    版本替代，避免把多来源冲突静默覆盖成单一结论（BC-40）。
    """
    if not is_trusted_adapter(source_adapter):
        raise ValueError(
            f"适配器 {source_adapter!r} 未注册。只有登记在案的结构化数据源才能"
            f"产出 structured_adapter 身份；通用网页检索/LLM 抽取不得登记。"
            f"当前已注册：{sorted(_TRUSTED_ADAPTERS)}"
        )
    if status not in EVIDENCE_REQUIRED_STATUSES:
        raise ValueError(
            f"status 必须是 {sorted(EVIDENCE_REQUIRED_STATUSES)} 之一，收到 {status!r}。"
            f"unverified / not_applicable 不主张事实，不得经由证据写入口产生"
        )
    if not raw:
        raise ValueError("raw 不得为空：适配器原始返回是人工追溯的唯一依据")
    cur = _timestamp_key(retrieved_at)
    if cur is None:
        raise ValueError(f"retrieved_at {retrieved_at!r} 不是合法 ISO 时间")
    # as_of_date 可以缺（旧适配器还没声明），但给了就必须合法——
    # 一个解析不出来的日期比没有更危险：它看起来像已经声明过了。
    if as_of_date and _timestamp_key(as_of_date) is None:
        raise ValueError(
            f"as_of_date {as_of_date!r} 不是合法 ISO 日期。该字段表示证据所述事实的"
            f"发布/事件日期，与 retrieved_at（何时取到）不是一回事"
        )

    field_id = field_check.get("field_id")
    if not field_id:
        raise ValueError("field_check 缺少 field_id")
    projected_patch = _project_registered_patch(source_adapter, field_id, raw)
    if projected_patch is not None:
        if profile_patch is not None and profile_patch != projected_patch:
            raise ValueError(
                "调用方提交的 profile_patch 与受信任适配器从 raw 确定性投影的结果不一致"
            )
        profile_patch = projected_patch
    elif profile_patch:
        raise ValueError(
            f"适配器 {source_adapter!r} 未登记 profile patch 投影器；"
            "调用方不得自由决定进入评分的数据"
        )
    _validate_profile_patch_shape(field_id, profile_patch)

    if status == STATUS_VERIFIED:
        if value in (None, ""):
            raise ValueError("verified 必须给出非空 value")
        if conflict_values:
            raise ValueError("verified 不得同时携带 conflict_values；有分歧就应标 conflicting")
    else:  # conflicting
        entries = conflict_values or []
        if value not in (None, ""):
            raise ValueError("conflicting 不得携带单一 value——那等于替复核人做了采信判断")
        sources, values = _distinct_conflict_pairs(entries)
        if len(sources) < 2 or len(values) < 2:
            raise ValueError(
                f"conflicting 需要至少两个不同来源的两个不同取值，"
                f"实际来源 {len(sources)} 个、取值 {len(values)} 个。"
                f"同源同值凑不出分歧"
            )

    # 先完成所有可能失败的计算，再一次性提交两个共享对象。此前 naive/aware
    # 时间比较会在写入一半后抛错，留下无法重放的半状态（BC-37）。
    previous_ids = list(field_check.get("evidence_ids") or [])
    declared_supersedes = list(supersedes_evidence_ids or [])
    if previous_ids:
        if set(declared_supersedes) != set(previous_ids):
            raise ValueError(
                "字段已有当前证据；更新结论时必须通过 supersedes_evidence_ids "
                "明确声明要替代的完整证据集合"
            )
        if not supersede_reason.strip():
            raise ValueError("替代已有证据时必须提供非空 supersede_reason，保留审计原因")
    elif declared_supersedes:
        raise ValueError("字段没有当前证据，不得声明 supersedes_evidence_ids")

    previous_times = []
    for eid in previous_ids:
        old = evidence_store.get(eid)
        if old is None:
            raise ValueError(f"当前证据 {eid!r} 不存在，不得通过新写入静默修复断链")
        if old.get("field_id") != field_id:
            raise ValueError(f"当前证据 {eid!r} 属于其他字段，不得通过新写入静默改写")
        if old.get("active") is False or old.get("superseded_by"):
            raise ValueError(f"当前证据 {eid!r} 已失效，不得再次作为被替代版本")
        old_ts = _timestamp_key(old.get("retrieved_at"))
        if old_ts is None:
            raise ValueError(f"当前证据 {eid!r} 缺少合法时间，不得通过新写入掩盖")
        previous_times.append((old_ts, eid))
    newest_previous = max((ts for ts, _ in previous_times), default=None)
    if newest_previous is not None and cur < newest_previous:
        raise ValueError(
            f"retrieved_at {retrieved_at!r} 早于当前有效证据时间，"
            "不得用旧数据覆盖新结论"
        )

    ev_id = new_evidence_id(field_id)
    new_evidence = {
        "evidence_id": ev_id,
        "field_id": field_id,
        "source_adapter": source_adapter,
        "retrieved_at": retrieved_at,
        "as_of_date": as_of_date,
        "value": value,
        "conflict_values": list(conflict_values or []),
        "profile_patch": deepcopy(profile_patch) if profile_patch else {},
        "raw": deepcopy(raw),
        "active": True,
        "supersedes": previous_ids,
        "supersede_reason": supersede_reason.strip(),
    }
    new_check = deepcopy(field_check)
    new_check["status"] = status
    new_check["verification_origin"] = ORIGIN_STRUCTURED_ADAPTER
    new_check["source_adapter"] = source_adapter
    new_check["failure_reason"] = failure_reason
    # evidence_ids 是“当前结论使用的证据”，不是无界历史列表。历史仍保留在
    # evidence_store，并以 active/superseded_by 显式标记（BC-38）。
    new_check["evidence_ids"] = [ev_id]
    if status == STATUS_VERIFIED:
        new_check["value"] = value
        new_check["conflict_detail"] = []
    else:
        new_check["value"] = None
        new_check["conflict_detail"] = [
            {"source": e.get("source"), "value": e.get("value"),
             "retrieved_at": e.get("retrieved_at", retrieved_at)}
            for e in (conflict_values or [])
        ]
    new_check["retrieved_at"] = retrieved_at
    # 事实/发布日期与取证时间语义不同，二者都要随当前结论进入清单。
    # Writer 引用事实日期，审计附录仍保留何时抓取；不得用运行日冒充发布日期。
    new_check["as_of_date"] = as_of_date

    for old_id in previous_ids:
        old = evidence_store.get(old_id)
        if old is not None:
            old["active"] = False
            old["superseded_by"] = ev_id
    evidence_store[ev_id] = new_evidence
    field_check.clear()
    field_check.update(new_check)
    return ev_id


def stamp_initial_profile_origin(
    field_checks: List[Dict],
    retrieved_at_by_field: Dict[str, str],
    *,
    profile_sources_by_field: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[str]:
    """
    给由初始档案填充的清单打上来源标记。

    在 `fill_field_checks()` 之后调用。只标记需要证据的状态——
    unverified 不主张任何事实，标了反而会让"有来源"失去含义。

    `profile_sources_by_field` 仅由已授权的管理端快照传入。给定时，时间必须
    来自**覆盖这个 field_id 的来源**：多个来源分别取最新的 ``retrieved_at``
    与最新的 ``as_of_date``，同时完整保留稳定排序后的来源列表。不能以别的
    字段或整份档案的时间来填补一个字段的来源链。

    静态/旧档案不会传 ``profile_sources_by_field``，保持历史行为：
    `retrieved_at` 取档案自身声明的时间，不能用 `datetime.now()`。后者记录的是
    程序读取时刻，把它当成证据获取时间就是拿运行时间冒充取证时间（BC-35）。
    档案没声明时留空，由重放校验记为降级，不伪造。

    Returns: 无法确定取证时间的 field_id 列表，供调用方披露。
    """
    def _latest_source_time(sources: List[Dict[str, Any]], key: str) -> str:
        candidates = []
        for source in sources:
            value = source.get(key)
            timestamp = _timestamp_key(value)
            if timestamp is not None:
                candidates.append((timestamp, value))
        return max(candidates, key=lambda row: row[0])[1] if candidates else ""

    undated: List[str] = []
    for c in field_checks:
        if c.get("status") not in EVIDENCE_REQUIRED_STATUSES:
            continue
        c["verification_origin"] = ORIGIN_INITIAL_PROFILE
        c["source_adapter"] = ORIGIN_INITIAL_PROFILE
        field_id = c.get("field_id")
        if profile_sources_by_field is None:
            ts = retrieved_at_by_field.get(field_id) or ""
        else:
            # An authorized snapshot with no source for this field must remain
            # undated.  Falling back to coverage here would let one field's
            # source silently authenticate another field's conclusion.
            sources = deepcopy(profile_sources_by_field.get(field_id, []))
            c["profile_sources"] = sources
            ts = _latest_source_time(sources, "retrieved_at")
            c["as_of_date"] = _latest_source_time(sources, "as_of_date")
        if parse_iso(ts) is None:
            ts = ""
            undated.append(field_id)
        c["retrieved_at"] = ts
        c.setdefault("evidence_ids", [])
    return undated


# ---------------------------------------------------------------- 重放校验

class ReplayReport:
    """
    重放结果。

    `mismatches` 必须触发 fail-closed；`degradations` 是需要显式披露、
    但不必然阻断的情况（旧检查点缺来源标记、档案未声明取证时间）。
    分开两个列表，是为了让"证据链断了"与"这是历史数据"在下游可区分——
    合成一个列表会逼调用方靠字符串匹配去猜。

    ⚠️ `degradations` 非空时，下游**不得**输出自动授信等级：见
       `risk_scorecard.apply_provenance_gate()`。降级只披露不约束，
       等于用一条日志换一个可能错误的放款决定（BC-33）。
    """

    def __init__(self) -> None:
        self.mismatches: List[Dict[str, Any]] = []
        self.degradations: List[Dict[str, Any]] = []

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> Dict[str, Any]:
        return {"mismatches": self.mismatches, "degradations": self.degradations}


def _replay_structured(
    check: Dict,
    evidence_store: Dict[str, Dict],
    as_of: str = "",
    degradations: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    按结构化证据重放。

    校验的不只是"证据存在"，还包括证据**确实属于这个字段、这个适配器、
    这个时间**——否则一条证据可以被任意字段借用，只要取值碰巧相同。
    """
    ev_ids = check.get("evidence_ids") or []
    if not ev_ids:
        return _fail(
            check, REASON_NO_EVIDENCE_IDS,
            "声称来自结构化适配器却没有 evidence_ids。"
            "attempted_sources 只表示尝试过，不构成核实证据",
            attempted_sources=check.get("attempted_sources") or [],
        )

    adapter = check.get("source_adapter")
    if not is_trusted_adapter(adapter):
        return _fail(
            check, REASON_UNTRUSTED_ADAPTER,
            f"适配器 {adapter!r} 不在受信任注册表内（已注册：{sorted(_TRUSTED_ADAPTERS)}）。"
            f"通用网页检索不能通过自称适配器获得结构化核实身份",
        )

    found = [evidence_store.get(e) for e in ev_ids]
    missing = [e for e, ev in zip(ev_ids, found) if ev is None]
    if missing:
        return _fail(
            check, REASON_EVIDENCE_NOT_FOUND,
            f"evidence_id 在证据库中不存在：{missing}",
            missing_evidence_ids=missing,
        )

    fid = check.get("field_id")
    for ev in found:
        if ev.get("active") is False or ev.get("superseded_by"):
            return _fail(
                check, REASON_EVIDENCE_INACTIVE,
                f"证据 {ev.get('evidence_id')} 已被 {ev.get('superseded_by')} 替代，"
                "历史证据不得继续支撑当前结论",
            )
        # —— 字段绑定：证据不得跨字段借用 ——
        if ev.get("field_id") != fid:
            return _fail(
                check, REASON_EVIDENCE_FIELD_MISMATCH,
                f"证据 {ev.get('evidence_id')} 属于字段 {ev.get('field_id')!r}，"
                f"不能用来支撑 {fid!r}。取值相同不代表说的是同一件事",
                evidence_field_id=ev.get("field_id"),
            )
        # —— 适配器绑定：证据来源必须与清单声明一致 ——
        if ev.get("source_adapter") != adapter:
            return _fail(
                check, REASON_EVIDENCE_ADAPTER_MISMATCH,
                f"清单声明来源 {adapter!r}，证据实际来自 {ev.get('source_adapter')!r}",
                evidence_adapter=ev.get("source_adapter"),
            )
        # —— 证据本体的时间戳：不能只查 check 上的那份副本 ——
        ev_ts = ev.get("retrieved_at")
        if not ev_ts:
            return _fail(
                check, REASON_EVIDENCE_MISSING_TIMESTAMP,
                f"证据 {ev.get('evidence_id')} 本体缺少 retrieved_at，无法判断时效",
            )
        if parse_iso(ev_ts) is None:
            return _fail(
                check, REASON_INVALID_TIMESTAMP,
                f"证据 {ev.get('evidence_id')} 的 retrieved_at {ev_ts!r} 非法 ISO 时间",
            )
        if not ev.get("raw"):
            return _fail(
                check, REASON_EVIDENCE_MISSING_RAW,
                f"证据 {ev.get('evidence_id')} 缺少 raw，人工复核无从追溯原始返回",
            )
        # —— 研究截止日：越界即阻断，日期不明只降级 ——
        # 越界是硬错误（用了截止日之后才存在的信息，判断本身失效）；
        # 日期不明是信息不足（可能合规也可能不合规），交人工判断。
        problem = check_as_of(ev, as_of)
        if problem is not None:
            reason, detail = problem
            entry = _fail(
                check, reason,
                f"证据 {ev.get('evidence_id')}：{detail}",
                evidence_as_of_date=ev.get("as_of_date", ""),
                research_as_of=as_of,
            )
            if reason == REASON_AS_OF_DATE_UNKNOWN:
                if degradations is not None:
                    degradations.append(entry)
            else:
                return entry
        # 写入时由注册投影器生成还不够：检查点/证据库可能事后漂移。
        # 重放必须再次从 raw 投影并与存储 patch 比对（BC-36）。
        try:
            projected = _project_registered_patch(adapter, fid, ev.get("raw"))
        except Exception as exc:
            return _fail(
                check, REASON_PATCH_RAW_MISMATCH,
                f"证据 {ev.get('evidence_id')} 无法从 raw 重建 profile_patch：{exc}",
            )
        stored_patch = ev.get("profile_patch") or {}
        if stored_patch and projected is None:
            return _fail(
                check, REASON_PATCH_RAW_MISMATCH,
                f"证据 {ev.get('evidence_id')} 保存了 profile_patch，"
                "但当前注册适配器无法从 raw 重建它",
            )
        if projected is not None and projected != stored_patch:
            return _fail(
                check, REASON_PATCH_RAW_MISMATCH,
                f"证据 {ev.get('evidence_id')} 的 profile_patch 与其 raw 的确定性投影不一致",
            )

    # —— check 上的时间戳必须等于最新一条证据的时间 ——
    latest = max(_timestamp_key(ev["retrieved_at"]) for ev in found)
    check_ts = _timestamp_key(check.get("retrieved_at"))
    if check_ts is None or check_ts != latest:
        return _fail(
            check, REASON_TIMESTAMP_MISMATCH,
            f"清单时间戳 {check.get('retrieved_at')!r} 与最新证据时间 "
            f"{latest.isoformat()}（统一为 UTC 后比较）不一致",
        )

    if check.get("status") == STATUS_CONFLICTING:
        all_conflicts = [cv for ev in found for cv in (ev.get("conflict_values") or [])]
        if len(all_conflicts) < 2:
            return _fail(
                check, REASON_MISSING_CONFLICT_VALUES,
                f"冲突状态但证据中的来源取值不足 2 条（实际 {len(all_conflicts)} 条），"
                f"无法还原分歧内容",
            )
        sources, values = _distinct_conflict_pairs(all_conflicts)
        if len(sources) < 2:
            return _fail(
                check, REASON_CONFLICT_SINGLE_SOURCE,
                f"冲突取值全部来自 {len(sources)} 个来源。同一来源内部的重复记录"
                f"不构成多源分歧",
            )
        if len(values) < 2:
            return _fail(
                check, REASON_CONFLICT_SILENTLY_RESOLVED,
                f"标为冲突但各来源取值实际只有 {len(values)} 个不同值——不存在分歧",
            )
        # —— conflict_detail 必须与证据一致，否则报告披露的分歧可能是另一回事 ——
        ev_pairs = {(c.get("source"), _hashable(c.get("value"))) for c in all_conflicts}
        chk_pairs = {(c.get("source"), _hashable(c.get("value")))
                     for c in (check.get("conflict_detail") or [])}
        if chk_pairs != ev_pairs:
            return _fail(
                check, REASON_CONFLICT_DETAIL_MISMATCH,
                "conflict_detail 与证据中的来源取值不一致，报告披露的分歧内容不可信",
                check_detail=sorted(map(str, chk_pairs)),
                evidence_detail=sorted(map(str, ev_pairs)),
            )
        return None

    # —— verified ——
    if check.get("value") in (None, ""):
        return _fail(
            check, REASON_VERIFIED_WITHOUT_VALUE,
            "verified 但清单没有取值，无法与证据比对",
        )
    values = [ev.get("value") for ev in found]
    distinct = {_hashable(v) for v in values if v not in (None, "")}
    if len(distinct) > 1:
        # 同一字段存在多个不同有效取值，却被单方面标成 verified：
        # 这正是系统一直防范的 conflict_silently_resolved，只是发生在证据层
        return _fail(
            check, REASON_CONFLICT_SILENTLY_RESOLVED,
            f"同一字段存在 {len(distinct)} 个不同的证据取值却被标为 verified。"
            f"多源分歧必须标 conflicting 并保留各方取值，不得单方面采信其一",
            evidence_values=values,
        )
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
    allow_legacy_profile_replay: bool = False,
    as_of: str = "",
) -> ReplayReport:
    """
    按 `verification_origin` 分发重放依据，校验证据链完整性。

    - `initial_profile`    → 从 company_profile 重放（复用 fill_field_checks）
    - `structured_adapter` → 从 evidence_store 重放
    - 缺来源（旧检查点）    → 走 legacy 路径，**显式记录降级**

    Args:
        allow_legacy_profile_replay:
            旧检查点缺少来源标记时，是否允许按档案重放兜底。
            **默认 False**：来源未迁移的检查点不得继续形成自动授信等级。
            置 True 仅用于显式迁移工具与非决策读取；即便如此，该情况**始终**
            进入 degradations，且下游必须施加来源闸门（BC-33）。
        as_of:
            研究截止日（ISO 日期）。留空表示不施加时点闸门，行为与引入本参数
            之前完全一致。给定时：证据事实日期晚于该日 → mismatch；
            证据未声明事实日期 → degradation。判据是 `as_of_date` 而非
            `retrieved_at`，理由见 `check_as_of()`。
    """
    evidence_store = evidence_store or {}
    report = ReplayReport()

    need_profile_replay: List[Dict] = []

    for check in field_checks:
        if check.get("status") not in EVIDENCE_REQUIRED_STATUSES:
            continue

        origin = check.get("verification_origin")

        # —— 旧检查点：显式降级，不静默猜测 ——
        if not origin:
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
                    "旧检查点缺少来源标记且未开启兜底重放（默认严格）",
                ))
            continue

        if origin not in VALID_ORIGINS:
            report.mismatches.append(_fail(
                check, REASON_INVALID_ORIGIN,
                f"来源 {origin!r} 不在合法集合 {sorted(VALID_ORIGINS)} 内。"
                f"通用网页检索与模型推断不构成字段级核实证据",
            ))
            continue

        # ``profile_sources`` is reserved for service-authorized
        # initial_profile snapshots.  A structured adapter already has its
        # own evidence_store chain; accepting this sidecar would let report
        # rendering disguise the adapter as an administrator source.
        if origin != ORIGIN_INITIAL_PROFILE and "profile_sources" in check:
            report.mismatches.append(_fail(
                check, REASON_UNEXPECTED_PROFILE_SOURCES,
                "非初始档案结论不得携带 profile_sources 字段来源链",
            ))
            continue

        if origin == ORIGIN_STRUCTURED_ADAPTER:
            # 结构化来源的时间戳是硬要求：证据本体与清单副本都要有且一致
            if not check.get("retrieved_at"):
                report.mismatches.append(_fail(
                    check, REASON_MISSING_TIMESTAMP,
                    "缺少 retrieved_at；无法判断证据时效，不予采信",
                ))
                continue
            problem = _replay_structured(
                check, evidence_store, as_of, report.degradations
            )
            if problem:
                report.mismatches.append(problem)
            continue

        # —— initial_profile ——
        # 档案未声明取证时间是既有数据的历史问题，记为降级而非阻断；
        # 与来源闸门配合后，它同样不会静默产出干净评级（BC-35）。
        ts = check.get("retrieved_at")
        if not ts:
            report.degradations.append(_fail(
                check, REASON_MISSING_TIMESTAMP,
                "初始档案未声明该项的取证时间，无法判断证据时效",
            ))
        elif parse_iso(ts) is None:
            report.mismatches.append(_fail(
                check, REASON_INVALID_TIMESTAMP,
                f"retrieved_at {ts!r} 不是合法 ISO 时间",
            ))
            continue
        elif as_of:
            # 管理端快照的字段来源链同时记录了事实/发布日期。存在该链时，
            # 截止日只能依 ``as_of_date`` 判断：晚事实 + 早抓取不能绕过闸门，
            # 晚抓取 + 早事实则仍可用于回溯研究。静态旧档案没有该字段，才按
            # 原兼容口径把快照 retrieved_at 当作事实日期。
            # 管理端快照身份来自服务验证后的 company capability，不能由
            # 检查点是否还保留 ``profile_sources`` 这个可删键决定。否则删除
            # 该键就能把新快照伪装成静态旧档案，退回 retrieved_at 口径。
            has_profile_sources = (
                company.get("_admin_profile_snapshot_authorized") is True
                or "profile_sources" in check
            )
            fact_date = check.get("as_of_date", "") if has_profile_sources else ts
            problem = check_as_of({"as_of_date": fact_date}, as_of)
            if problem is not None:
                reason, detail = problem
                entry = _fail(
                    check, reason,
                    f"初始档案字段事实日期 {fact_date or '未声明'}：{detail}",
                    research_as_of=as_of,
                )
                # 日期不明是信息不足而非确凿越界，与结构化证据保持同一
                # 三档语义。对于已授权字段来源链，随后 provenance 重放仍会
                # 捕捉遭删除/漂移的 as_of_date，不能借此静默放行。
                if reason == REASON_AS_OF_DATE_UNKNOWN:
                    report.degradations.append(entry)
                else:
                    report.mismatches.append(entry)
                    continue
        need_profile_replay.append(check)

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
            if check.get("status") == STATUS_VERIFIED and exp.get("value") != check.get("value"):
                report.mismatches.append(_fail(
                    check, REASON_PROFILE_REPLAY_MISMATCH,
                    "初始档案重放出的取值与清单不一致",
                    check_value=check.get("value"),
                    profile_value=exp.get("value"),
                ))
                continue
            # 字段级来源链是结论的一部分，而不只是展示信息。若只重放值，
            # 改掉 source_id/reference/sha256 或把某字段的时点换成别的字段的
            # 时点都会保持"值相同"而绕过审计。新管理端快照带
            # ``profile_sources``；静态/旧档案没有它，保持其既有重放兼容性。
            if "profile_sources" in check or "profile_sources" in exp:
                provenance_fields = ("profile_sources", "retrieved_at", "as_of_date")
                changed = {
                    key: {"check": check.get(key), "profile": exp.get(key)}
                    for key in provenance_fields
                    if check.get(key) != exp.get(key)
                }
                if changed:
                    report.mismatches.append(_fail(
                        check, REASON_PROFILE_REPLAY_MISMATCH,
                        "初始档案重放出的字段级来源链与检查点不一致",
                        provenance_difference=changed,
                    ))
    elif need_profile_replay:
        for check in need_profile_replay:
            report.mismatches.append(_fail(
                check, REASON_PROFILE_REPLAY_MISMATCH,
                "需要档案重放但未提供重放函数",
            ))

    return report


# ---------------------------------------------------------------- 评分视图

def _merge_patch(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """
    把证据的档案片段合并进评分视图。

    列表**追加**而非替换：适配器是在贡献证据，不是在接管整份档案。
    追加可能重复计数（风险偏高），替换则可能抹掉档案里已有的负面记录
    （风险偏低）。在授信场景里偏高是可接受的保守方向，偏低不是。
    """
    for k, v in patch.items():
        cur = target.get(k)
        if isinstance(cur, list) and isinstance(v, list):
            target[k] = cur + deepcopy(v)
        elif isinstance(cur, dict) and isinstance(v, dict):
            merged = dict(cur)
            merged.update(deepcopy(v))
            target[k] = merged
        else:
            target[k] = deepcopy(v)


def build_scoring_view(
    company: Dict[str, Any],
    field_checks: List[Dict],
    evidence_store: Optional[Dict[str, Dict]] = None,
    *,
    profile_backed_fields: frozenset,
    profile_replay_fn: Optional[Callable] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    构造评分卡真正消费的那份结构化数据。

    ## 为什么必须有这一层

    评分卡的取值来自 `company_profile`，而核实状态来自 `field_checks`。
    结构化适配器只写后者时，`_ok("guarantee")` 为真、`company["guarantee"]`
    仍为空 → 评分卡输出"未发现对外担保"。**一条新增的负面证据被翻译成了
    正面结论**，且完整度闸门查不出来（清单确实是 verified）。这是 BC-19
    的同形复发，只是入口从"档案没进 state"换成了"证据没进档案"（BC-31）。

    Returns:
        (view, unmergeable)
        `unmergeable` 非空时调用方**必须** fail-closed：宁可不予评级，
        也不能拿一份缺了证据的档案去算分。
    """
    evidence_store = evidence_store or {}
    view = deepcopy(company)
    unmergeable: List[Dict[str, Any]] = []

    for check in field_checks:
        if check.get("status") not in EVIDENCE_REQUIRED_STATUSES:
            continue
        if check.get("verification_origin") != ORIGIN_STRUCTURED_ADAPTER:
            continue
        fid = check.get("field_id")
        if fid not in profile_backed_fields:
            # 该字段的风险贡献不从档案取值（如纯状态型判断），无需合并
            continue

        # conflicting 不参与具体分值计算，由冲突闸门上调等级；把互相矛盾的
        # patch 合并进评分档案反而会形成一份不存在的“综合事实”。
        if check.get("status") == STATUS_CONFLICTING:
            continue

        current_evidence = [
            evidence_store.get(e) or {}
            for e in (check.get("evidence_ids") or [])
        ]
        patches = [ev.get("profile_patch") for ev in current_evidence]
        applied = [p for p in patches if p]
        if not applied:
            unmergeable.append(_fail(
                check, REASON_EVIDENCE_NOT_MERGEABLE,
                f"字段 {fid} 的风险分由结构化档案计算，但其证据没有提供 "
                f"profile_patch，合并后评分卡仍会读到旧数据并可能得出相反结论",
            ))
            continue
        for p in applied:
            try:
                _validate_profile_patch_shape(fid, p)
            except ValueError as exc:
                unmergeable.append(_fail(
                    check, REASON_PATCH_FIELD_MISMATCH,
                    f"字段 {fid} 的 profile_patch 越权或结构非法：{exc}",
                ))
                continue

            if profile_replay_fn is None:
                unmergeable.append(_fail(
                    check, REASON_PATCH_VALUE_MISMATCH,
                    f"字段 {fid} 无法使用生产清单映射重放 profile_patch；"
                    "仅凭 patch 非空不足以证明它与证据结论一致",
                ))
                continue

            # 在“仅含本 patch”的最小档案上运行生产字段映射。这样比较的是
            # patch 自己表达的结论，而不是它与旧档案合并后的聚合文案。
            # 例如 value 声称“存在1800万担保”而 patch 写 guarantee=[]，
            # 会被重放成“经查询，无相关记录”并 fail-closed（BC-36）。
            patch_company = deepcopy(p)
            patch_company.update({
                "name": company.get("name", "结构化证据重放"),
                "credit_code": company.get("credit_code", ""),
                "coverage": {"queried": [fid], "retrieved_at": check.get("retrieved_at", "")},
            })
            try:
                predicted = profile_replay_fn(patch_company, field_checks).get(fid) or {}
            except Exception as exc:
                unmergeable.append(_fail(
                    check, REASON_PATCH_VALUE_MISMATCH,
                    f"字段 {fid} 的 profile_patch 无法通过生产映射重放："
                    f"{type(exc).__name__}: {exc}",
                ))
                continue
            if (predicted.get("status") != STATUS_VERIFIED
                    or _normalized_display(predicted.get("value"))
                    != _normalized_display(check.get("value"))):
                unmergeable.append(_fail(
                    check, REASON_PATCH_VALUE_MISMATCH,
                    f"字段 {fid} 的 profile_patch 经生产映射重放后得到 "
                    f"{predicted.get('status')}/{predicted.get('value')!r}，"
                    f"与证据清单 {check.get('status')}/{check.get('value')!r} 不一致",
                    patch_status=predicted.get("status"),
                    patch_value=predicted.get("value"),
                ))
                continue
            _merge_patch(view, p)

    return view, unmergeable
