"""
担保圈推导适配器（v0.7-C）—— 解除 BC-18 的能力缺失

## 这个适配器与其它适配器的区别

其它适配器**查询**数据源；这个适配器**推导**。它的输入是关联关系库里
所有主体的担保关系，输出是经过尽调对象的环。

因此它的 `source_adapter` 仍然是受信任注册表里的一员，但它的
`raw` 不是某个接口的返回，而是**推导过程的完整记录**：
遍历到的节点、走过的边、找到的环、以及**没能展开的分支**。
最后一项才是关键——人工复核必须能看出这次推导有多完整。

## 为什么它让「低风险」第一次可达

BC-18 的结论是：系统若确实无法核查某个必查项，它本来就不该出具低风险结论。
解除条件写死在断言里——**靠建能力解除，不靠调阈值**。本适配器就是那个能力。

`guarantee_circle` 随之从 `not_implemented` 改为 `available`，
能力缺失闸门不再对所有企业恒亮。
"""
import json
import os
from typing import Any, Dict, Optional

try:
    from service.datasource.base import AdapterResult, DataSourceAdapter
    from service.guarantee_graph import (
        GuaranteeGraph, detect_guarantee_circles, profile_records,
    )
except ImportError:  # 兼容以 app 为包根的导入方式
    from app.service.datasource.base import AdapterResult, DataSourceAdapter
    from app.service.guarantee_graph import (
        GuaranteeGraph, detect_guarantee_circles, profile_records,
    )

_DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "data", "sources", "relation_registry.json",
)


class GuaranteeCircleAdapter(DataSourceAdapter):
    adapter_id = "graph_analysis"
    description = "担保圈图谱推导：从关联关系库的担保边检测互保与连环担保"
    covers = frozenset({"guarantee_circle"})

    def __init__(self) -> None:
        super().__init__()
        self._cache: Optional[Dict[str, Any]] = None

    def _load(self) -> Dict[str, Any]:
        if self._cache is None:
            with open(os.path.normpath(_DATA_FILE), encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache

    def fetch(self, company: Dict[str, Any]) -> Optional[AdapterResult]:
        data = self._load()
        records = data.get("companies") or {}
        code = company.get("credit_code")
        me = records.get(code)
        if me is None:
            # 主体不在关联关系库中，无法推导。**不返回空结果**——
            # "库里没有这个主体"与"这个主体没有担保圈"是完全不同的结论
            return None

        graph = GuaranteeGraph.from_records(records)
        report = detect_guarantee_circles(me["company_name"], graph)

        raw = {
            "subject": me["company_name"],
            "circles": profile_records(report),
            "circle_detail": report.circles,
            "traversal_complete": report.traversal_complete,
            "unknown_counterparties": report.unknown_counterparties,
            "depth_truncated": report.depth_truncated,
            "visited_nodes": report.visited,
            "knowledge_graph": report.as_graph(),
            "circle_text": report.describe() if report.has_circle else "",
            "describe": report.describe(),
        }
        if not report.traversal_complete:
            # 遍历不完整 → 本项视为**未查询**，保持信息缺口。
            # 把没走完的遍历说成"未发现担保圈"，就是把查不到当成没问题。
            return AdapterResult(
                queried=[], raw=raw, retrieved_at=me.get("retrieved_at", ""),
                not_queried_reason={"guarantee_circle": (
                    f"担保圈推导未能闭合：对手方 {report.unknown_counterparties} "
                    f"未在关联关系库中登记，无法确认其是否反向担保，"
                    f"不得据此判定未涉入担保圈")},
            )
        return AdapterResult(queried=["guarantee_circle"], raw=raw,
                             retrieved_at=me.get("retrieved_at", ""))

    def extract(self, field_id: str, raw: Dict[str, Any]) -> Optional[str]:
        if field_id != "guarantee_circle":
            return None
        # 有环时给出完整链路；无环时返回 None，由基类写入系统标准措辞
        # 「经查询，无相关记录」——这与 `fill_field_checks` 的重放结果一致，
        # 自造一句更好听的措辞会让 patch 一致性校验判为漂移。
        # 更详细的推导过程保留在证据 raw 里，供人工复核追溯。
        return raw.get("circle_text") or None

    @staticmethod
    def project_profile_patch(field_id: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        投影为评分卡消费的 `company["guarantee_circle"]`。

        无环时返回空列表而非 None：patch 不只是"改变数据"，
        更是声明这条证据能被评分卡消费——「查了，无担保圈」同样是要进
        评分视图的结论（v0.7-B 踩过这个坑）。
        """
        if field_id != "guarantee_circle":
            return None
        return {"guarantee_circle": [dict(c) for c in (raw.get("circles") or [])]}
