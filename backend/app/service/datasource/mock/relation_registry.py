# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
关联关系登记适配器（mock，v0.7-B）

## 为什么第一个真实适配器选它

1. **头寸最大**：五家评测企业里 `related_party` 5/5 未核实、
   `external_investment` 4/5 未核实——relation 是唯一整体塌陷的维度
2. **是 C 阶段的数据前置**：担保圈检测需要企业间的担保关系，
   没有关联关系数据就无从推导（BC-18 的解除条件）
3. **能真正改变评分**：`guarantee` 属 `PROFILE_BACKED_FIELDS`，
   它的证据必须能并进评分档案，会走完整的 `profile_patch` 投影链路

## 数据边界

数据源文件与企业档案**物理分离**。这不是洁癖：适配器若从
`companies.json` 读数据，它就只是换了个壳的档案读取器，
v0.6a 那套证据链仍然没有被真实检验过。

`EVAL-004`（主体存疑）刻意不在数据源中——该主体在工商源就查不到，
关联关系源同样查不到它。**适配器不得填补为测试而设计的缺口。**
"""
import json
import os
from typing import Any, Dict, Optional

try:
    from service.datasource.base import AdapterResult, DataSourceAdapter
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.datasource.base import AdapterResult, DataSourceAdapter

_DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "data", "sources", "relation_registry.json",
)


class RelationRegistryAdapter(DataSourceAdapter):
    adapter_id = "relation_registry"
    description = "关联关系登记库（mock）：关联方、对外投资、对外担保"
    covers = frozenset({"related_party", "external_investment", "guarantee"})

    def __init__(self) -> None:
        super().__init__()
        self._cache: Optional[Dict[str, Any]] = None

    def _load(self) -> Dict[str, Any]:
        if self._cache is None:
            with open(os.path.normpath(_DATA_FILE), encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def fetch(self, company: Dict[str, Any]) -> Optional[AdapterResult]:
        code = company.get("credit_code")
        if not code:
            return None
        rec = (self._load().get("companies") or {}).get(code)
        if rec is None:
            # 查不到主体。**不返回空结果**——那会被下游读成"查了但没有"，
            # 而"这个主体在关联关系库里根本不存在"是完全不同的信息。
            return None
        return AdapterResult(
            queried=rec.get("queried") or [],
            raw=rec,
            retrieved_at=rec.get("retrieved_at", ""),
            not_queried_reason=rec.get("not_queried_reason") or {},
        )

    def extract(self, field_id: str, raw: Dict[str, Any]) -> Optional[str]:
        """无记录返回 None，由基类按字段类型决定它是正面结论还是异常。"""
        if field_id == "related_party":
            rows = raw.get("related_party") or []
            return "；".join(
                f"{r['name']}（{r['relation']}）" + (f"：{r['note']}" if r.get("note") else "")
                for r in rows
            ) or None
        if field_id == "external_investment":
            rows = raw.get("external_investment") or []
            return "；".join(f"{r['name']} 持股{r['ratio']:.2%}" for r in rows) or None
        if field_id == "guarantee":
            rows = raw.get("guarantee") or []
            return "；".join(
                f"为{g['beneficiary']}提供{g['guarantee_type']}{g['amount']}{g.get('unit', '万元')}"
                f"（{g.get('period', '期限未载明')}，{g.get('board_resolution', '内部决议情况未载明')}）"
                for g in rows
            ) or None
        return None

    @staticmethod
    def project_profile_patch(field_id: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        只有 `guarantee` 需要并入评分档案——评分卡从 `company["guarantee"]`
        取笔数与金额算 relation 维度。

        `related_party` / `external_investment` 不在 `PROFILE_BACKED_FIELDS` 里，
        评分卡不从档案取它们的值，因此返回 None：**不该进评分的数据就不要
        塞进评分视图**，patch 越权是 BC-36。
        """
        if field_id != "guarantee":
            return None
        # ⚠️ 无担保记录时**必须**返回空列表 patch，不能返回 None。
        #
        # 起初我按"合并空列表不改变任何结论"的理由返回了 None，实测直接
        # 触发 `evidence_not_mergeable`：`guarantee` 属 PROFILE_BACKED_FIELDS，
        # 一条没有 patch 的证据会让整份评级 fail-closed（BC-31 的保护）。
        #
        # 想错的地方在于：patch 的作用不只是"改变数据"，更是**声明这条证据
        # 能被评分卡消费**。「查了，无担保」本身就是一条要进评分视图的结论。
        # 合并语义是追加，空列表也不会抹掉档案里已有的记录。
        return {"guarantee": [dict(g) for g in (raw.get("guarantee") or [])]}
