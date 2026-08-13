"""
数据源适配层（v0.7-B）

## 为什么现在才做

适配层从 Stage 1 推到 v0.4、又推到现在。中间 v0.6a 花了三轮复核，
为它建起完整的证据链：来源闭集、受信任适配器注册表、证据绑定校验、
评分视图合并。**但那套东西至今只对着测试替身验证过**——
`_TRUSTED_ADAPTERS` 一直是空的。

这与 BC-45 是同一形态在架构层的复现：机制建好了，却从没被真实的东西用过。
本层的第一个价值就是**让证据链第一次处理非替身的适配器**。

## 适配器的契约

一个适配器必须回答清楚三件事，且**三者不可混为一谈**：

| 情形 | 结论 | 依据 |
|---|---|---|
| 查了，有内容 | `verified` + 取值 + 证据 | 数据源返回了记录 |
| 查了，无内容（事件型） | `verified` +「经查询，无相关记录」 | 可以合法地不存在 |
| 查了，无内容（属性型） | `unverified` + 异常信号 | 存续企业必然具备，空 = 主体存疑 |
| **没查** | `unverified` + 信息缺口 | 该源不覆盖此项 |

第三与第四行的区分是 v0.2 撞出来的核心设计（`coverage.queried`）：
把"没查"当成"查了没有"会直接误导授信审批。

## 边界：适配器不得自行决定进入评分的数据

`project_profile_patch` 必须是**纯函数**，从 `raw` 确定性投影出档案片段，
并登记在注册表里。调用方不能自由构造 patch——那等于让"什么数据进入风险评分"
由调用点决定（BC-36）。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

try:
    from service.verification import record_structured_evidence, register_adapter
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.verification import record_structured_evidence, register_adapter


class AdapterResult:
    """
    一次数据源查询的结果。

    `queried` 与 `records` 分开表达，是为了让"没查"与"查了没有"在类型层就分开——
    用一个空列表同时表示两种含义，是 v0.2 那个设计漏洞的源头。
    """

    def __init__(
        self,
        *,
        queried: Sequence[str],
        raw: Dict[str, Any],
        retrieved_at: str,
        not_queried_reason: Optional[Dict[str, str]] = None,
    ):
        self.queried = frozenset(queried)
        self.raw = raw or {}
        self.retrieved_at = retrieved_at
        self.not_queried_reason = not_queried_reason or {}

    def __repr__(self) -> str:
        return (f"AdapterResult(queried={sorted(self.queried)}, "
                f"retrieved_at={self.retrieved_at!r})")


class DataSourceAdapter(ABC):
    """
    结构化数据源适配器基类。

    子类必须声明 `adapter_id` / `description` / `covers`，并实现
    `fetch` / `extract` / `project_profile_patch`。

    `register()` 把自己登记进受信任注册表——**只有登记过的适配器才能产出
    `structured_adapter` 身份**。通用网页检索与 LLM 抽取永远不得登记：
    它们产出自然语言，无法在重放时做字段级等值比对（v0.6a 规则 2）。
    """

    adapter_id: str = ""
    description: str = ""
    #: 本适配器负责的 field_id 集合。不在其中的字段一律不碰。
    covers: frozenset = frozenset()

    def __init__(self) -> None:
        if not self.adapter_id or not self.covers:
            raise ValueError(f"{type(self).__name__} 必须声明 adapter_id 与 covers")

    # ---------------------------------------------------------- 子类实现

    @abstractmethod
    def fetch(self, company: Dict[str, Any]) -> Optional[AdapterResult]:
        """
        查询数据源。查不到主体返回 None——**不得返回空结果冒充"查了没有"**。
        """

    @abstractmethod
    def extract(self, field_id: str, raw: Dict[str, Any]) -> Optional[str]:
        """
        从 raw 抽出该字段的展示取值。无记录返回 None。

        返回 None 的含义是"该源查了但这一项没有内容"，
        由 `apply()` 按字段类型决定它是正面结论还是异常信号。
        """

    @staticmethod
    @abstractmethod
    def project_profile_patch(field_id: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        从 raw 确定性投影出可并入评分档案的片段。纯函数，无副作用。

        只有 `PROFILE_BACKED_FIELDS` 里的字段需要它；其余返回 None。
        形状受 `verification._validate_profile_patch_shape` 强制校验。
        """

    # ---------------------------------------------------------- 通用逻辑

    def register(self) -> None:
        register_adapter(
            self.adapter_id, self.description,
            project_profile_patch=self.project_profile_patch,
        )

    def apply(
        self,
        company: Dict[str, Any],
        field_checks: List[Dict[str, Any]],
        evidence_store: Dict[str, Dict],
    ) -> Dict[str, str]:
        """
        对清单施加本适配器的核查结论。

        ⚠️ 只处理 `covers` 内、且**当前尚未核实**的字段。
        已由初始档案核实的项不覆盖——覆盖需要显式的证据替代授权（BC-40），
        而"我后跑"不构成替代理由。

        Returns: {field_id: 处理结果说明}，供调用方记录与断言。
        """
        try:
            from config.dd_checklist import CHECKLIST_BY_ID
        except ImportError:
            from app.config.dd_checklist import CHECKLIST_BY_ID

        result = self.fetch(company)
        outcome: Dict[str, str] = {}
        if result is None:
            # 查不到主体本身是信息，但不是"无记录"——不动任何字段状态
            for fid in sorted(self.covers):
                outcome[fid] = "adapter_no_subject"
            return outcome

        by_id = {c["field_id"]: c for c in field_checks}
        for fid in sorted(self.covers):
            chk = by_id.get(fid)
            if chk is None or chk.get("status") in ("verified", "conflicting",
                                                    "not_applicable"):
                outcome[fid] = "skipped_already_settled"
                continue
            if fid not in result.queried:
                # 该源没查这一项：保持信息缺口，写明原因，绝不当作"无记录"
                chk["failure_reason"] = result.not_queried_reason.get(
                    fid, f"数据源 {self.adapter_id} 未覆盖该项")
                self._note_attempt(chk)
                outcome[fid] = "not_queried"
                continue

            value = self.extract(fid, result.raw)
            item = CHECKLIST_BY_ID.get(fid)
            if value is None:
                if item is not None and not item.absence_meaningful:
                    # 属性型字段返回空 = 主体存疑，绝不能粉饰成"经查询无记录"
                    chk["failure_reason"] = (
                        f"数据源 {self.adapter_id} 已查询但未返回该项内容。"
                        f"此为必备属性，缺失属异常信号，须人工核实主体真实性")
                    self._note_attempt(chk)
                    outcome[fid] = "attribute_missing_anomaly"
                    continue
                value = "经查询，无相关记录"

            record_structured_evidence(
                evidence_store, chk,
                source_adapter=self.adapter_id,
                status="verified",
                value=value,
                raw=result.raw,
                retrieved_at=result.retrieved_at,
            )
            self._note_attempt(chk)
            outcome[fid] = "verified"
        return outcome

    def _note_attempt(self, chk: Dict[str, Any]) -> None:
        attempted = chk.setdefault("attempted_sources", [])
        if self.adapter_id not in attempted:
            attempted.append(self.adapter_id)
