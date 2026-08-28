"""Text-PDF RAG -> fixed checklist -> structured evidence regressions."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.graph import DeepResearchGraph  # noqa: E402
from service.deep_research_v2.agents.writer import LeadWriter  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402
from service.deep_research_v2.state import create_initial_state  # noqa: E402
from service.rag_evidence_bridge import (  # noqa: E402
    collect_analysis_evidence,
    finalize_rag_evidence,
)
from config.dd_checklist import is_core_check  # noqa: E402


SUBJECT = "Example Technology Co Ltd"


def _state(*, cutoff="2025-05-31"):
    state = create_initial_state(
        "Perform due diligence",
        "test-session",
        subject_name=SUBJECT,
        business_type="factoring",
        due_diligence=True,
        as_of=cutoff,
    )
    graph = object.__new__(DeepResearchGraph)
    graph._load_company_profile(state["query"], state)
    return state


def _result(value="362,012,554", *, period="2024年度", source="S1", date="2025-03-15"):
    quote = f"{SUBJECT} {period}营业收入 {value} 千元。"
    text = (
        f"[case_id=test; source_id={source}; locator=page:1]\n"
        f"标题：{SUBJECT} annual report\n发布日期：{date}\n{quote}"
    )
    return {
        "is_local": True,
        "summary": text,
        "title": f"{SUBJECT} annual report",
        "url": f"local://kb/test/{source}",
        "site_name": "test-kb",
    }, quote


def _analysis(value="362,012,554", *, period="2024年度", index=1, quote=None,
              doctored_quote=None):
    """按**新契约**构造模型输出（BC-57）。

    模型只交锚点：引用哪一条结果、字段、取值、期间。`exact_quote` 与 `unit`
    不再由模型提供——引文由系统从原文切出，单位由系统从表头解析。

    `doctored_quote` 只用于反向用例：证明模型即使硬塞一段伪造引文，
    系统也一律忽略，不会因此打开新的信任路径。
    """
    content = quote or f"{SUBJECT} {period}营业收入 {value} 千元。"
    field_row = {
        "field_id": "revenue",
        "period": period,
        "value": value,
        "numeric_value": value,
        "source_result_index": index,
    }
    if doctored_quote is not None:
        field_row["exact_quote"] = doctored_quote
        field_row["unit"] = "亿元"          # 同样必须被忽略
    return {
        "extracted_facts": [{
            "content": content,
            "source_url": "model-supplied-url-is-not-trusted",
            "source_result_index": index,
        }],
        "field_evidence": [field_row],
    }


def _check(state, field_id):
    return next(check for check in state["field_checks"] if check["field_id"] == field_id)


def test_unknown_company_still_enters_fixed_due_diligence_flow():
    state = _state()
    assert state["company_name"] == SUBJECT
    assert state["company_profile"]["_dynamic_rag_subject"] is True
    assert "credit_application" not in state["company_profile"]
    # 断言核心二十项齐全，而不是断言清单总长度：装置的 business_type 是
    # factoring，会确定性带入场景扩展项（BC-58）。总长度会随场景变化，
    # "核心二十项恒在、必查项恒为 15" 才是不该变的那个不变量。
    core = [check for check in state["field_checks"] if is_core_check(check)]
    assert len(core) == 20
    assert len(state["field_checks"]) > 20, "factoring 业务应带入场景扩展项"
    assert state["completeness"]["required_total"] == 15
    assert state["completeness"]["required_verified"] == 0
    assert state["completeness"]["scenario"]["name"] == "factoring"


def test_generic_unknown_query_does_not_accidentally_enable_due_diligence():
    state = create_initial_state("Research an industry", "generic")
    graph = object.__new__(DeepResearchGraph)
    assert graph._load_company_profile(state["query"], state) is None
    assert state["field_checks"] == []


def test_due_diligence_uses_deterministic_report_without_llm_completion():
    state = _state()
    state["phase"] = ResearchPhase.WRITING.value
    state["outline"] = [{"id": "sec_1", "title": "企业基本情况", "status": "pending"}]
    agent = LeadWriter("unused", "http://localhost:1", "test")

    async def forbidden(*args, **kwargs):
        raise AssertionError("零已核实证据时不得调用 Writer LLM")

    agent.call_llm = forbidden
    asyncio.run(agent.process(state))

    report = state["final_report"]
    assert "必查项核实率为 0/15" in report
    assert "统一社会信用代码已核实" not in report
    assert "不得据此放款" in report
    assert state["phase"] == ResearchPhase.REVIEWING.value


def test_deterministic_report_only_prints_verified_values_and_real_provenance():
    state = _state()
    check = _check(state, "actual_controller")
    check.update(status="verified", value="Alice", source_adapter="rag_text_document_v1",
                 as_of_date="2025-03-15", evidence_ids=["ev-controller"])
    state["evidence_store"]["ev-controller"] = {
        "active": True, "field_id": "actual_controller",
        "source_adapter": "rag_text_document_v1", "as_of_date": "2025-03-15",
        "raw": {"sources": [{"source": "local://kb/test/S1", "source_id": "S1",
                              "locator": "page:1", "exact_quote": "actual controller Alice"}]},
    }
    state["phase"] = ResearchPhase.WRITING.value
    state["outline"] = [{"id": "sec_2", "title": "股权结构", "status": "pending"}]
    agent = LeadWriter("unused", "http://localhost:1", "test")

    async def forbidden(*args, **kwargs):
        raise AssertionError("尽调最终报告不得调用 Writer LLM")

    agent.call_llm = forbidden
    asyncio.run(agent.process(state))
    report = state["final_report"]
    assert "实际控制人：已核实" in report and "Alice" in report
    assert "business_registry - 内部工商数据库接口" not in report


def test_verbatim_local_fact_promotes_check_and_persists_auditable_evidence():
    state = _state()
    result, quote = _result(period="2024年度", source="S1")
    analysis = _analysis(quote=quote)
    state["facts"].append({
        "content": quote, "source_url": "model-supplied-url-is-not-trusted",
        "verified": False, "related_sections": ["sec_4"], "metadata": {},
    })

    collect_analysis_evidence(state, analysis, [result], "sec_4")
    for period, value, source in (
        ("2023年度", "400,917,045", "S2"),
        ("2022年度", "328,593,856", "S3"),
    ):
        extra_result, extra_quote = _result(value, period=period, source=source)
        collect_analysis_evidence(
            state, _analysis(value, period=period, quote=extra_quote), [extra_result], "sec_4"
        )
    counts = finalize_rag_evidence(state)

    check = _check(state, "revenue")
    assert counts["verified"] == 1
    assert check["status"] == "verified"
    assert check["source_adapter"] == "rag_text_document_v1"
    assert check["as_of_date"] == "2025-03-15"
    evidence = state["evidence_store"][check["evidence_ids"][0]]
    assert evidence["raw"]["sources"][0]["exact_quote"] == quote
    assert {row["period"]: row["revenue"] for row in evidence["profile_patch"]["financials"]}[
        "2024年度"
    ] == 36201255.4
    assert state["facts"][0]["verified"] is True
    assert state["facts"][0]["source_url"] == "local://kb/test/S1"


def test_same_financial_value_in_different_units_is_not_a_conflict():
    state = _state()
    first, quote1 = _result("507.45", period="2024年度", source="S1")
    quote1 = f"{SUBJECT} 2024年度净利润 507.45 亿元。"
    first["summary"] = first["summary"].replace(
        f"{SUBJECT} 2024年度营业收入 507.45 千元。", quote1
    )
    second, _ = _result("50,745,000", period="2024年度", source="S2")
    quote2 = f"{SUBJECT} 2024年度净利润 50,745,000 千元。"
    second["summary"] = second["summary"].replace(
        f"{SUBJECT} 2024年度营业收入 50,745,000 千元。", quote2
    )
    for result, quote, value, unit, index in (
        (first, quote1, "507.45", "亿元", 1),
        (second, quote2, "50,745,000", "千元", 2),
    ):
        collect_analysis_evidence(state, {
            "extracted_facts": [],
            "field_evidence": [{
                "field_id": "net_profit", "period": "2024年度", "value": value,
                "numeric_value": value, "unit": unit,
                "source_result_index": index, "exact_quote": quote,
            }],
        }, [first, second], "sec_4")

    counts = finalize_rag_evidence(state)
    assert counts["conflicting"] == 0
    assert _check(state, "net_profit")["status"] == "unverified"
    assert "1 个期间" in _check(state, "net_profit")["failure_reason"]


def test_financial_unit_is_read_from_quote_not_model_claim():
    state = _state()
    result, _ = _result("50,745,000", period="2024年度", source="S1")
    quote = f"{SUBJECT} 归母净利润（千元） 50,745,000。"
    result["summary"] = result["summary"].replace(
        f"{SUBJECT} 2024年度营业收入 50,745,000 千元。", quote
    )
    analysis = {"extracted_facts": [], "field_evidence": [{
        "field_id": "net_profit", "period": "2024年度", "value": "50,745,000",
        "numeric_value": "50,745,000", "unit": "元",
        "source_result_index": 1, "exact_quote": quote,
    }]}
    collect_analysis_evidence(state, analysis, [result], "sec_4")
    assert state["rag_evidence_candidates"][0]["unit"] == "千元"


def test_post_cutoff_source_is_rejected_even_when_the_value_is_real():
    """截止日闸门单独成例。

    原来这一条和"伪造引文被拒"合在一个用例里，而装置同时把日期设成了
    截止日之后——**日期闸门先命中，伪造引文那一半从未真正被验证过**。
    一个因为错误原因通过的断言，和没有断言的差别很小。
    """
    state = _state()
    result, _ = _result(date="2025-07-01")           # 截止日 2025-05-31
    collect_analysis_evidence(state, _analysis(), [result], "sec_4")
    assert not state["rag_evidence_candidates"]
    assert any("晚于研究截止日" in row["reason"] for row in state["rag_evidence_rejections"])
    finalize_rag_evidence(state)
    assert _check(state, "revenue")["status"] == "unverified"


def test_value_absent_from_the_source_is_rejected_with_a_valid_date():
    """幻觉闸门单独成例：来源日期合法，只有取值是编的。

    新契约下模型不再交引文，所以幻觉闸门锚在**取值**上：取值在原文里
    定位不到，就切不出窗口，候选整条被拒。
    """
    state = _state()
    result, _ = _result(date="2025-03-15")           # 日期合法
    invented = _analysis("999,999,999")              # 原文里没有这个数
    collect_analysis_evidence(state, invented, [result], "sec_4")
    assert not state["rag_evidence_candidates"], "编造的取值必须过不去"
    # 类别与明细分开断言：`reason` 是稳定类别（用于聚合统计），
    # 取值这类具体信息走 `detail`。把取值拼进 reason 会让同类失败在统计里
    # 碎成一堆计数 1 的条目，"最大卡点是什么"就答不出来了。
    rejections = state["rag_evidence_rejections"]
    assert any(row["reason"] == "无法从原文切出证据窗口" for row in rejections)
    assert any("无法在原文中逐字定位" in row["detail"] for row in rejections),         "明细要说清是哪一步失败，否则无法区分编造取值与引错编号"
    assert any("999,999,999" in row["value"] for row in rejections)


def test_model_supplied_quote_and_unit_are_ignored_not_trusted():
    """接口改造不得留下新的信任路径。

    模型硬塞一段伪造引文和一个错单位，系统必须一律忽略：存档的引文只能是
    系统从原文切出来的那一段，单位只能是从原文表头解析出来的那个。
    """
    state = _state()
    result, real_quote = _result(date="2025-03-15")
    analysis = _analysis(doctored_quote=f"{SUBJECT} 2024年度营业收入 999 亿元。")
    collect_analysis_evidence(state, analysis, [result], "sec_4")

    assert state["rag_evidence_candidates"], "取值真实存在，候选本身应当通过"
    accepted = state["rag_evidence_candidates"][0]
    assert "999" not in accepted["quote"], "伪造引文不得被存档"
    assert accepted["quote"] in result["summary"], "存档引文必须来自原文"
    assert accepted["unit"] == "千元", "单位必须来自原文表头，不得采信模型自报的亿元"


def test_generated_quote_is_always_a_contiguous_substring_of_the_source():
    """系统切出的窗口一定是原文连续子串——这是逐字保证的新载体。

    此前这个保证依赖"模型能无损复制"，而模型不能；现在它由切片这个动作
    本身提供，无法被模型行为破坏。
    """
    state = _state()
    for value, period in (("362,012,554", "2024年度"), ("400,917,045", "2023年度")):
        result, _ = _result(value, period=period, date="2025-03-15")
        collect_analysis_evidence(state, _analysis(value, period=period), [result], "sec_4")
        window = state["rag_evidence_candidates"][-1]["quote"]
        assert window in result["summary"]
        assert value.replace(",", "") in window.replace(",", "")


def test_unit_header_far_from_the_value_is_still_resolved():
    """BC-57 的核心复现：年报把单位写在表头，数据行只有数字。

    真实年报的扁平化表格长这样——`单位：千元` 在表头，`营业收入` 与数值在
    几百字符之后的数据行。旧实现要求单位与数值出现在同一段引文里，模型
    只能把表头和数据行拼起来，而拼接产物不是原文连续子串，必然被拒。
    这一条在修复前必然失败。
    """
    state = _state()
    filler = "\n".join(f"其他项目{i} 说明文字" for i in range(40))
    source_text = (
        "[case_id=test; source_id=S9; locator=page:18]\n"
        f"标题：{SUBJECT} annual report\n发布日期：2025-03-15\n"
        "合并利润表\n单位：千元\n"
        f"{filler}\n"
        "营业收入 362,012,554 400,917,045 -9.70%\n"
    )
    result = {"is_local": True, "summary": source_text,
              "title": "S009_S009.pdf",       # 上传文件名，不含主体
              "url": "local://kb/test/S9", "site_name": "test-kb"}
    assert source_text.index("单位：千元") + 400 < source_text.index("362,012,554"), \
        "装置必须真的把表头与数值拉开距离，否则这一条测不到东西"

    collect_analysis_evidence(state, _analysis("362,012,554"), [result], "sec_4")
    assert state["rag_evidence_candidates"], \
        "表头与数值不同行不该让整条证据丢失——这正是 BC-57 的漏损"
    accepted = state["rag_evidence_candidates"][0]
    assert accepted["unit"] == "千元"
    assert accepted["quote"] in source_text, "引文仍必须是原文连续子串"
    assert "单位：千元" in accepted["unit_quote"], "单位取证原文要单独留档"


def test_record_anchor_keys_match_canonical_record_requirements():
    """窗口锚点与记录重建要求必须同源。

    两处对不上就会出现自相矛盾的拒绝：窗口没覆盖某个关键值，随后又因为
    "记录无法从原文重建"把它拒掉，而根因是系统自己没把那个值圈进窗口。
    """
    from service import rag_evidence_bridge as bridge

    for field_id, keys in bridge._RECORD_KEYS.items():
        record = {key: f"值{i}" for i, key in enumerate(keys)}
        quote = "；".join(record.values())
        assert bridge._canonical_record(field_id, {"record": record}, quote) is not None, \
            f"{field_id} 的锚点键与 _canonical_record 的必填键不一致"


def test_traditional_title_and_filename_still_confirm_the_subject():
    """主体归属是文档属性，不是某一页的属性（BC-57 的一处假拒绝）。

    港交所招股书标题是繁体，年报附注页根本不写公司全称，检索结果标题是
    上传文件名。旧实现要求主体名出现在片段正文里，于是"这一页没写公司名"
    被记成了"无法确认属于本主体"。真正的隔离边界是 kb_scope。
    """
    state = _state()
    source_text = (
        "[case_id=test; source_id=S6; locator=page:351]\n"
        f"标题：{SUBJECT} prospectus\n发布机构：{SUBJECT}\n发布日期：2025-03-15\n"
        "营业收入 362,012,554 千元\n"
    )
    result = {"is_local": True, "summary": source_text, "title": "S006_S006.pdf",
              "url": "local://kb/test/S6", "site_name": "test-kb"}
    collect_analysis_evidence(state, _analysis("362,012,554"), [result], "sec_4")
    assert state["rag_evidence_candidates"], \
        "文档头部已确认主体，正文未复述公司名不构成拒绝理由"


def test_a_document_belonging_to_another_company_is_still_rejected():
    """反面：文档级确认不得退化成不确认。"""
    state = _state()
    source_text = (
        "[case_id=test; source_id=S7; locator=page:1]\n"
        "标题：Unrelated Holdings Ltd annual report\n发布机构：Unrelated Holdings Ltd\n"
        "发布日期：2025-03-15\n营业收入 362,012,554 千元\n"
    )
    result = {"is_local": True, "summary": source_text, "title": "other.pdf",
              "url": "local://kb/test/S7", "site_name": "test-kb"}
    collect_analysis_evidence(state, _analysis("362,012,554"), [result], "sec_4")
    assert not state["rag_evidence_candidates"]
    assert any("无法确认属于当前尽调主体" in row["reason"]
               for row in state["rag_evidence_rejections"])


def test_record_values_spread_across_unrelated_rows_are_rejected():
    """记录型字段不得跨记录拼装。

    窗口要求全部关键值落在同一段连续原文里。分散在相距很远的不同行时，
    切不出窗口——这比"让模型自己保证不拼装"可靠，因为它是机器判定的。
    """
    state = _state()
    filler = "\n".join(f"无关行{i}" for i in range(200))
    source_text = (
        "[case_id=test; source_id=S8; locator=page:88]\n"
        f"标题：{SUBJECT} annual report\n发布日期：2025-03-15\n"
        "案号 （2024）闽09民初123号\n"
        f"{filler}\n"
        "买卖合同纠纷 1,200 已结案\n"
    )
    result = {"is_local": True, "summary": source_text, "title": "S008.pdf",
              "url": "local://kb/test/S8", "site_name": "test-kb"}
    collect_analysis_evidence(state, {"extracted_facts": [], "field_evidence": [{
        "field_id": "litigation", "value": "（2024）闽09民初123号",
        "source_result_index": 1,
        "record": {"case_no": "（2024）闽09民初123号", "cause": "买卖合同纠纷",
                   "amount": "1,200", "status": "已结案"},
    }]}, [result], "sec_4")
    assert not state["rag_evidence_candidates"]
    rejections = state["rag_evidence_rejections"]
    assert any(row["reason"] == "无法从原文切出证据窗口" for row in rejections)
    assert any("跨度" in row["detail"] for row in rejections),         "记录要素散落在不相干行时，明细必须点明是跨度超限而不是取值不存在"


def test_same_period_different_sources_becomes_conflicting():
    state = _state()
    first, first_quote = _result("362,012,554", source="S1")
    second, second_quote = _result("400,917,045", source="S2")
    collect_analysis_evidence(state, _analysis("362,012,554", index=1, quote=first_quote),
                              [first, second], "sec_4")
    collect_analysis_evidence(state, _analysis("400,917,045", index=2, quote=second_quote),
                              [first, second], "sec_4")

    counts = finalize_rag_evidence(state)
    check = _check(state, "revenue")
    assert counts["conflicting"] == 1
    assert check["status"] == "conflicting"
    assert len(check["conflict_detail"]) == 2
    assert state["evidence_store"][check["evidence_ids"][0]]["profile_patch"] == {}
