# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""财务数据的**报表口径**判定：合并 vs 母公司。

## 为什么必须有这一层

真实运行里系统把母公司报表的应收账款当成了公司的应收账款：

    报告采用  72,225,597 千元   （S002 page:221，十八、母公司财务报表主要项目注释）
    合并口径  66,776,402 千元   （S002 page:167，七、合并财务报表项目注释）

**现有闸门一道都拦不住**：逐字对、主体对（都是宁德时代）、单位对（千元）、
截止日对、字段关键词「应收账款」就在数字旁边。全部通过。

唯一没有被验证的是：这张表属于哪份报表。

这比幻觉危险——数字是真的、引用是准的、来源是权威的，只是取自错误的报表。
逐字校验对它完全无效，因为它不是编造。而对保理业务这是要害：母公司口径
含对子公司的内部往来，合并时要抵消，**那部分根本不可融**。

形态与 BC-51 同族：一个对决策至关重要的区分，在类型系统里不存在。

## 判据怎么校准的

按 case_01 真实语料统计，`合并利润表` 这类词出现 8 次，其中多数是正文交叉
引用（`详见"第十节 财务报告"之"七、合并财务报表项目注释"`、
`将其现金流量纳入合并现金流量表`）。只有带**编号前缀**且在行首的才是分节标题：

    七、合并财务报表项目注释          ← 是
    十八、母公司财务报表主要项目注释    ← 是
    1、合并资产负债表                ← 是
    利润纳入合并利润表                ← 否（无编号）
    之"七、合并财务报表项目注释"       ← 否（不在行首）

不带这个约束，一句交叉引用就会把整段文档的口径判反——判据没校准就会把
正确的东西判成错的（BC-60 一族）。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence, Tuple

CONSOLIDATED = "consolidated"
PARENT = "parent"
UNKNOWN = "unknown"

# 编号前缀 + 口径关键字 + 报表/注释名。必须在行首。
_HEADER = re.compile(
    r"^[ \t　]*(?:[0-9]{1,2}|[一二三四五六七八九十]{1,3})[、.．]\s*"
    r"(合并|母公司)"
    r"(?:财务报表[^\n]{0,8}注释|资产负债表|利润表|现金流量表|所有者权益变动表|股东权益变动表)",
    re.MULTILINE,
)

_SCOPE_BY_WORD = {"合并": CONSOLIDATED, "母公司": PARENT}


class ScopeIndex:
    """一份文档的口径分界点，按文档顺序排列。"""

    def __init__(self, transitions: Sequence[Tuple[int, int, str]]):
        # (chunk_index, char_offset, scope)
        self._transitions = sorted(transitions, key=lambda row: (row[0], row[1]))

    def __len__(self) -> int:
        return len(self._transitions)

    @property
    def transitions(self) -> List[Tuple[int, int, str]]:
        return list(self._transitions)

    def has_parent_section(self) -> bool:
        """这份文档是否存在母公司/合并之分。

        没有这个区分的文档（公告、摘要、内控报告）不需要口径判定，
        `unknown` 在那里是良性的；有区分的文档里 `unknown` 必须视为不合格，
        因为"分不清"和"是合并"是两件事——又一次"空值同时是结论和故障"。
        """
        return any(scope == PARENT for _, _, scope in self._transitions)

    def resolve(self, chunk_index: int, offset: int = 0) -> str:
        """取该位置**之前最近**的一个分节标题所声明的口径。"""
        current = UNKNOWN
        for c_index, c_offset, scope in self._transitions:
            if (c_index, c_offset) <= (chunk_index, offset):
                current = scope
            else:
                break
        return current

    def scope_at_chunk_start(self, chunk_index: int) -> str:
        """进入该切片时的口径（切片内的分界点不算）。"""
        current = UNKNOWN
        for c_index, _offset, scope in self._transitions:
            if c_index < chunk_index:
                current = scope
            else:
                break
        return current

    def marks_inside(self, chunk_index: int) -> List[Tuple[int, str]]:
        """该切片**内部**的分界点 [(offset, scope), ...]，通常为空。"""
        return [(offset, scope) for c_index, offset, scope in self._transitions
                if c_index == chunk_index]


def build_scope_index(chunks: Iterable[Tuple[int, str]]) -> ScopeIndex:
    """从同一文档的切片построить口径索引。

    Args:
        chunks: (chunk_index, 切片原文) 的可迭代对象，顺序无所谓，内部会排序。
    """
    transitions: List[Tuple[int, int, str]] = []
    for chunk_index, text in chunks:
        for match in _HEADER.finditer(text or ""):
            scope = _SCOPE_BY_WORD.get(match.group(1))
            if scope:
                transitions.append((int(chunk_index), match.start(), scope))
    return ScopeIndex(transitions)


def resolve_in_chunk(
    scope_at_start: str, marks_inside: Sequence[Tuple[int, str]], offset: int
) -> str:
    """在切片内按取值位置定口径。

    只需要"进入切片时的口径"+"切片内的分界点"两样，都可 JSON 序列化——
    检索结果要经过 fixture 落盘和 SSE 推送，不能往里塞对象。
    """
    current = scope_at_start or UNKNOWN
    for mark_offset, scope in sorted(marks_inside or []):
        if mark_offset <= offset:
            current = scope
        else:
            break
    return current


def scope_for_value(
    index: Optional[ScopeIndex], chunk_index: int, text: str, value: str
) -> str:
    """判定某个取值在文档里的口径。

    取值可能在切片内多次出现；取**第一次**出现的位置——同一切片内跨越分节
    标题的情况极少，且取靠前的更保守（分节标题在前，值在后）。
    """
    if index is None or not len(index):
        return UNKNOWN
    offset = text.find(value) if value else -1
    return index.resolve(chunk_index, max(offset, 0))
