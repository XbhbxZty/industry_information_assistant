"""
担保圈推导（纯函数，无 LLM）—— BC-18 的解除条件

## 为什么这件事必须是规则而非模型

担保圈是监管明确关注的系统性风险。评审会要问"凭什么说它涉入担保圈"，
答案必须是"A 为 B 担保 2400 万，B 为 C 担保 1800 万，C 又为 A 担保 2000 万，
构成 3 环"，而不是"模型认为"。与 `risk_scorecard.py` 同一原则。

## 一条比"检测出环"更重要的规则：未发现 ≠ 不存在

图谱推导的"未发现担保圈"**只和图的完整性一样强**。若某个担保对手方
根本不在关联关系库里，我们无从知道它是否反向担保——此时不得表述为
"未发现担保圈"，那是把"查不到"说成"没问题"，正是这个项目一直在防的东西。

因此结论分三种，**必须严格区分**：

| 情形 | 结论 | 依据 |
|---|---|---|
| 主体无任何对外担保 | 确定性地不涉入 | 担保圈需要主体自身是担保人，无出边则无环 |
| 遍历闭合且无环 | 未发现担保圈 | 所有对手方都在库中且都已展开 |
| **对手方不在库中 / 深度截断** | **无法判定** | 遍历不完整，不得下"未发现"的结论 |

第三种情形返回 `traversal_complete=False`，由适配器落成 `unverified`
而非 verified——宁可留一个信息缺口，也不出一个没有依据的正面结论。
"""
from typing import Any, Dict, List, Optional, Sequence

# 环的最大长度。超过此深度停止展开并标记遍历不完整——
# 不是为了性能，而是为了让"没找到"与"没找完"在结果里可分辨。
MAX_CIRCLE_DEPTH = 6

CIRCLE_MUTUAL = "互保"          # 2 环：A ⇄ B
CIRCLE_CHAIN = "连环担保"        # 3 环及以上


class GuaranteeGraph:
    """
    担保关系有向图：A --担保--> B 表示 A 为 B 提供担保。

    以**企业名称**为节点标识：担保记录里的对手方只有名称，
    没有统一社会信用代码。这是真实数据源的常态，也是一个已知弱点——
    同名主体会被合并（与 BC-10 同类风险），真实接入时需要以信用代码对齐。
    """

    def __init__(self) -> None:
        self.edges: Dict[str, List[Dict[str, Any]]] = {}
        self.known: set = set()

    @classmethod
    def from_records(cls, records: Dict[str, Dict[str, Any]]) -> "GuaranteeGraph":
        """
        `records` 是 {credit_code: 关联关系登记记录}。
        只消费 `guarantee` 字段——关联方与对外投资不构成担保责任。
        """
        g = cls()
        for rec in (records or {}).values():
            name = rec.get("company_name")
            if not name:
                continue
            g.known.add(name)
            for gu in rec.get("guarantee") or []:
                target = gu.get("beneficiary")
                if not target:
                    continue
                g.edges.setdefault(name, []).append({
                    "to": target,
                    "amount": gu.get("amount"),
                    "unit": gu.get("unit", "万元"),
                    "guarantee_type": gu.get("guarantee_type", ""),
                    "period": gu.get("period", ""),
                })
        return g

    def out_edges(self, name: str) -> List[Dict[str, Any]]:
        return self.edges.get(name) or []


class CircleReport:
    """担保圈推导结果。`traversal_complete=False` 时任何"未发现"都不成立。"""

    def __init__(
        self,
        *,
        subject: str,
        circles: List[Dict[str, Any]],
        traversal_complete: bool,
        unknown_counterparties: Sequence[str] = (),
        depth_truncated: bool = False,
        visited: Sequence[str] = (),
    ):
        self.subject = subject
        self.circles = circles
        self.traversal_complete = traversal_complete
        self.unknown_counterparties = sorted(set(unknown_counterparties))
        self.depth_truncated = depth_truncated
        self.visited = sorted(set(visited))

    @property
    def has_circle(self) -> bool:
        return bool(self.circles)

    def describe(self) -> str:
        """人类可读结论。供报告正文与证据取值使用。"""
        if self.circles:
            parts = []
            for c in self.circles:
                chain = " → ".join(c["path"] + [c["path"][0]])
                parts.append(f"{c['kind']}：{chain}（环上担保合计 {c['total_amount']:.0f} 万元）")
            return "；".join(parts)
        if not self.traversal_complete:
            return ""      # 无法判定，调用方不得据此下结论
        return "经关联关系图谱推导，未发现互保或连环担保"

    def as_graph(self) -> Dict[str, Any]:
        """
        导出为知识图谱结构，供前端担保圈可视化。

        这是知识图谱组件第一次有真实业务目的——此前它只是通用实体图。
        """
        on_circle = {n for c in self.circles for n in c["path"]}
        nodes = [{
            "id": n, "label": n, "type": "company",
            "is_subject": n == self.subject,
            "on_circle": n in on_circle,
        } for n in self.visited]
        edges = [{
            "source": e["from"], "target": e["to"], "relation": "担保",
            "amount": e.get("amount"), "unit": e.get("unit", "万元"),
            "on_circle": e.get("on_circle", False),
        } for e in self._flat_edges]
        return {"nodes": nodes, "edges": edges}

    _flat_edges: List[Dict[str, Any]] = []


def _classify(path: List[str]) -> str:
    return CIRCLE_MUTUAL if len(path) == 2 else CIRCLE_CHAIN


def detect_guarantee_circles(
    subject: str,
    graph: GuaranteeGraph,
    max_depth: int = MAX_CIRCLE_DEPTH,
) -> CircleReport:
    """
    找出所有经过 `subject` 的担保环。

    主体无对外担保时直接返回"确定性不涉入"：担保圈要求主体自身是担保人，
    没有出边就不可能成环——这个结论**不依赖图的完整性**，
    因此即便对手方数据缺失也成立。
    """
    if not graph.out_edges(subject):
        return CircleReport(subject=subject, circles=[], traversal_complete=True,
                            visited=[subject])

    circles: List[Dict[str, Any]] = []
    unknown: List[str] = []
    visited: List[str] = [subject]
    flat_edges: List[Dict[str, Any]] = []
    truncated = False

    def dfs(node: str, path: List[str], acc: List[Dict[str, Any]]) -> None:
        nonlocal truncated
        for e in graph.out_edges(node):
            nxt = e["to"]
            visited.append(nxt)
            flat_edges.append({"from": node, **e})
            if nxt == subject:
                edges_on_circle = acc + [{"from": node, **e}]
                circles.append({
                    "path": list(path),
                    "kind": _classify(path),
                    "edges": edges_on_circle,
                    "total_amount": sum(float(x.get("amount") or 0) for x in edges_on_circle),
                })
                continue
            if nxt in path:
                # 不经过 subject 的环，与本主体无关，不展开
                continue
            if nxt not in graph.known:
                # 对手方不在库中：无从知道它是否反向担保
                unknown.append(nxt)
                continue
            if len(path) >= max_depth:
                truncated = True
                continue
            dfs(nxt, path + [nxt], acc + [{"from": node, **e}])

    dfs(subject, [subject], [])

    # 标记环上的边，供图谱高亮
    on_circle_pairs = {(e["from"], e["to"]) for c in circles for e in c["edges"]}
    for e in flat_edges:
        e["on_circle"] = (e["from"], e["to"]) in on_circle_pairs

    report = CircleReport(
        subject=subject, circles=circles,
        # 发现了环就是确定性结论，不受未展开分支影响；
        # 没发现环时，遍历必须完整才能下"未发现"
        traversal_complete=bool(circles) or (not unknown and not truncated),
        unknown_counterparties=unknown, depth_truncated=truncated, visited=visited,
    )
    report._flat_edges = flat_edges
    return report


def profile_records(report: CircleReport) -> List[Dict[str, Any]]:
    """
    投影为评分卡消费的 `company["guarantee_circle"]` 结构。

    评分卡按环的条数打分（见 `risk_scorecard.score`），
    因此这里一条环一条记录，并保留金额与路径供报告与人工复核追溯。
    """
    return [{
        "kind": c["kind"],
        "path": c["path"],
        "total_amount": c["total_amount"],
        "unit": "万元",
        "hops": len(c["path"]),
    } for c in report.circles]
