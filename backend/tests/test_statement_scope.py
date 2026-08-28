# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
报表口径闸门：拦住"数字真实但取自母公司报表"

## 这一轮在钉什么

真实运行里系统把母公司报表的应收账款当成了公司的应收账款：

    报告采用  72,225,597 千元  （S002 page:221，十八、母公司财务报表主要项目注释）
    合并口径  66,776,402 千元  （S002 page:167，七、合并财务报表项目注释）

**既有闸门一道都没拦住**：逐字对、主体对（都是宁德时代）、单位对（千元）、
截止日对、字段关键词「应收账款」就在数字旁边。全部通过。唯一没被验证的是
这张表属于哪份报表。

这比幻觉危险——数字真实、引用准确、来源权威，只是取自错误的报表。逐字校验
对它完全无效。对保理业务更是要害：母公司口径含对子公司的内部往来，合并时
抵消，那部分根本不可融，报告因此高估可融应收基数 8.2%。

形态与 BC-51 同族：一个对决策至关重要的区分，在类型系统里不存在。

运行：cd backend && python -m pytest tests/test_statement_scope.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.statement_scope import (  # noqa: E402
    CONSOLIDATED, PARENT, UNKNOWN, build_scope_index, resolve_in_chunk,
)

# 照真实年报的写法构造：编号前缀 + 行首
CONSOLIDATED_HEADER = "七、合并财务报表项目注释"
PARENT_HEADER = "十八、母公司财务报表主要项目注释"
# 真实语料里的交叉引用形态，必须**不**被当成分节标题
CROSS_REF = '详见"第十节 财务报告"之"七、合并财务报表项目注释""24、所有权"'
INLINE = "将其现金流量纳入合并现金流量表，把利润纳入合并利润表"


# --------------------------------------------------- 一、分节标题识别的校准

def test_numbered_line_start_headers_are_recognised():
    index = build_scope_index([
        (10, f"公司简介\n{CONSOLIDATED_HEADER}\n1、货币资金"),
        (50, f"分部信息\n{PARENT_HEADER}\n1、应收账款"),
    ])
    assert len(index) == 2
    assert index.resolve(30) == CONSOLIDATED
    assert index.resolve(60) == PARENT


def test_cross_references_are_not_treated_as_headers():
    """本条判据最容易写错的地方。

    `合并财务报表项目注释` 在 case_01 语料出现 9 次，多数是正文交叉引用。
    不排除它们，一句"详见…七、合并财务报表项目注释"就会把母公司分节
    整段判反——判据没校准就会把正确的东西判成错的（BC-60 一族）。
    """
    index = build_scope_index([(10, f"某段正文 {CROSS_REF}\n{INLINE}")])
    assert len(index) == 0, f"交叉引用被误认成分节标题：{index.transitions}"


def test_statement_titles_with_numbering_count():
    index = build_scope_index([(5, "（一）财务报表\n1、合并资产负债表\n单位：千元")])
    assert index.resolve(5, 999) == CONSOLIDATED


# ------------------------------------------- 二、切片内与切片间的位置解析

def test_scope_resolves_by_position_inside_a_chunk():
    assert resolve_in_chunk(CONSOLIDATED, [(500, PARENT)], 200) == CONSOLIDATED
    assert resolve_in_chunk(CONSOLIDATED, [(500, PARENT)], 900) == PARENT


def test_unknown_before_any_header():
    """第一个分节标题之前 = 年报正文/摘要，没有口径标记。"""
    index = build_scope_index([(10, f"正文\n{CONSOLIDATED_HEADER}")])
    assert index.resolve(5) == UNKNOWN
    assert index.scope_at_chunk_start(10) == UNKNOWN


def test_document_without_parent_section_is_flagged():
    """一季报只有合并报表，没有母公司分节——那里 unknown 是良性的。"""
    plain = build_scope_index([(1, "1、合并资产负债表")])
    both = build_scope_index([(1, "1、合并资产负债表"), (9, PARENT_HEADER)])
    assert not plain.has_parent_section()
    assert both.has_parent_section()


# ------------------------------------ 三、端到端：真实缺陷必须被闸门拦住

def _dd_state():
    from service.deep_research_v2.graph import DeepResearchGraph
    from service.deep_research_v2.state import create_initial_state
    state = create_initial_state(
        "尽调", "scope-test", subject_name="宁德时代新能源科技股份有限公司",
        business_type="factoring", due_diligence=True, as_of="2025-05-31",
    )
    graph = object.__new__(DeepResearchGraph)
    graph._load_company_profile(state["query"], state)
    return state


def _result_row(scope: str, value: str):
    text = (
        "[case_id=case_01; source_id=S002; locator=page:221]\n"
        "标题：宁德时代新能源科技股份有限公司2024年年度报告全文\n"
        "发布日期：2025-03-15\n"
        "单位：千元\n"
        f"账龄 期末账面余额 期初账面余额\n合计 {value} 69,980,342\n"
    )
    return {
        "is_local": True, "summary": text, "url": "local://kb/case_01/d1",
        "title": "S002_S002.pdf", "site_name": "本地知识库",
        "doc_id": "d1", "chunk_index": 221, "statement_scope": scope,
    }


def _candidate(value: str):
    return {"field_evidence": [{
        "field_id": "accounts_receivable_gross", "period": "2024年末",
        "value": value, "numeric_value": value, "source_result_index": 1,
    }]}


def test_parent_company_value_is_rejected():
    """本条的直接回归：母公司口径的应收账款必须被拒。"""
    from service.rag_evidence_bridge import collect_analysis_evidence
    state = _dd_state()
    collect_analysis_evidence(
        state, _candidate("72,225,597"), [_result_row(PARENT, "72,225,597")], "sec_4")
    assert not state.get("rag_evidence_candidates"), "母公司口径的值必须过不去"
    reasons = [r["reason"] for r in state["rag_evidence_rejections"]]
    assert any("母公司报表" in r for r in reasons), \
        f"必须以口径为由拒绝，而不是别的原因：{reasons}"


def test_consolidated_value_is_accepted_and_scope_recorded():
    """互为反面：合并口径必须放行，且口径要留档供审计。"""
    from service.rag_evidence_bridge import collect_analysis_evidence
    state = _dd_state()
    collect_analysis_evidence(
        state, _candidate("66,776,402"),
        [_result_row(CONSOLIDATED, "66,776,402")], "sec_4")
    cands = state.get("rag_evidence_candidates") or []
    assert cands, f"合并口径不该被拒：{state.get('rag_evidence_rejections')}"
    assert cands[0]["statement_scope"] == CONSOLIDATED


def test_unknown_scope_is_accepted_but_recorded():
    """正文/摘要（第一个分节标题之前）放行，但口径如实记为 unknown。"""
    from service.rag_evidence_bridge import collect_analysis_evidence
    state = _dd_state()
    collect_analysis_evidence(
        state, _candidate("66,776,402"),
        [_result_row(UNKNOWN, "66,776,402")], "sec_4")
    cands = state.get("rag_evidence_candidates") or []
    assert cands, "unknown 不应被拒——否则年报正文的数据会全军覆没"
    assert cands[0]["statement_scope"] == UNKNOWN


def test_gate_only_applies_to_consolidated_required_fields():
    """非财务字段不受口径约束，不能顺手扩大打击面。"""
    from service.rag_evidence_bridge import CONSOLIDATED_REQUIRED_FIELDS
    assert "accounts_receivable_gross" in CONSOLIDATED_REQUIRED_FIELDS
    assert "registration" not in CONSOLIDATED_REQUIRED_FIELDS
    assert "actual_controller" not in CONSOLIDATED_REQUIRED_FIELDS


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn(); print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1; print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
