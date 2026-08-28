# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
调查层（B 层）—— 双轨产出计划阶段 1

## 这一轮在钉什么

尽调模式此前把图表、知识图谱、趋势分析整块关掉了，产出是一份只有表格的
报告。阶段 1 把它们接回来，前提是**一道闸门都不松**。

因此这里钉的不是"能画出图"，而是画出来的图**不会被误读为裁决依据**：

1. 分类与来源是必填，缺失直接抛错——不给默认值，因为一张没标来源的
   探索性图表与确定性图表在视觉上毫无区别（红线 2）
2. 解析不干净就不画——两点当三点画出来的是一条会被当作完整趋势看的折线
3. 单位混用就不画——万元与亿元同框时图形本身就是错的，且错得看不出来
4. B 层发现必须过**代码判定**的主体确认，不采信模型自报（红线 3）
5. 失败要留痕，且区分「查了没有」与「没查成」（BC-51 / 计划 9.2）

运行：cd backend && python -m pytest tests/test_investigation_layer.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service import investigation_layer as inv  # noqa: E402


# ------------------------------------------------- 一、时间序列解析

def test_parses_the_verified_value_string_produced_by_the_profile():
    """解析的就是清单里那个已核实取值串，不是另一份数据。"""
    parsed = inv.parse_period_series(
        "2023年度 41250.0万元；2024年度 46800.0万元；2025年度 50120.0万元")
    assert parsed["refusal"] == ""
    assert parsed["unit"] == "万元"
    assert [p["period"] for p in parsed["points"]] == ["2023年度", "2024年度", "2025年度"]
    assert [p["value"] for p in parsed["points"]] == [41250.0, 46800.0, 50120.0]


def test_percentage_series_keeps_its_unit():
    parsed = inv.parse_period_series("2023年度 55.1%；2024年度 60.2%；2025年度 63.3%")
    assert parsed["unit"] == "%"
    assert parsed["points"][-1]["value"] == pytest.approx(63.3)


def test_original_number_text_is_preserved_verbatim():
    """`41250.0` 不得在正文里被渲染成 `41250`。

    同一个数在图上与附录里有两种写法，就是 BC-59 那种表示漂移。
    数值给图表用，原文给正文用。
    """
    parsed = inv.parse_period_series("2023年度 41250.0万元；2024年度 46800.0万元")
    assert [p["text"] for p in parsed["points"]] == ["41250.0", "46800.0"]


def test_partial_parse_refuses_instead_of_drawing_what_it_got():
    """三个区间只认出两个时，整条拒绝。

    画出来的两点折线会被当作完整趋势看——与 `unratable()` 同一原则：
    给出一个有数字的产物，读者就会当它是结论。
    """
    parsed = inv.parse_period_series(
        "2023年度 41250.0万元；2024年度 数据缺失；2025年度 50120.0万元")
    assert parsed["points"] == []
    assert "无法确定性解析" in parsed["refusal"]
    assert parsed["unparsed"], "被拒的片段要留痕，否则无从判断是解析器的问题还是资料的问题"


def test_mixed_units_refuse_because_the_shape_itself_would_be_wrong():
    parsed = inv.parse_period_series("2023年度 4.1亿元；2024年度 46800.0万元")
    assert parsed["points"] == []
    assert "多个单位" in parsed["refusal"]


def test_single_period_is_not_a_trend():
    parsed = inv.parse_period_series("2025年度 50120.0万元")
    assert parsed["points"] == []
    assert "不构成趋势" in parsed["refusal"]


# ------------------------------------------------- 二、分类与来源强制

def test_chart_without_provenance_is_refused_at_construction():
    """缺来源直接抛错，而不是给个默认值让它静默通过。"""
    with pytest.raises(ValueError):
        inv.make_chart(inv.CHART_DETERMINISTIC, "line", "营业收入趋势", provenance={})


def test_deterministic_chart_must_name_the_checklist_field():
    with pytest.raises(ValueError):
        inv.make_chart(inv.CHART_DETERMINISTIC, "line", "营业收入趋势",
                       provenance={"note": "随便写的"})


def test_exploratory_chart_must_carry_sources():
    with pytest.raises(ValueError):
        inv.make_chart(inv.CHART_EXPLORATORY, "line", "行业均价",
                       provenance={"field_id": "revenue"})


def test_exploratory_badge_goes_into_the_title_not_only_the_footnote():
    """徽标必须进标题。

    脚注在截图、导出、缩略图里都会丢失，标题不会——而红线 2 保护的
    正是"读者不会去看脚注"这件事。
    """
    chart = inv.make_chart(
        inv.CHART_EXPLORATORY, "line", "行业均价",
        series=[{"period": "2024", "value": 1}, {"period": "2025", "value": 2}],
        provenance={"sources": [{"title": "某行业报告", "published_at": "2025-06-01"}]})
    assert chart["title"].startswith(inv.EXPLORATORY_BADGE)
    assert "未通过证据闸门" in chart["note"]
    assert chart["plain_title"] == "行业均价", "同时要保留不带徽标的原名供前端使用"


# ------------------------------------------------- 三、B 层发现准入

_SOURCE = {
    "title": "云岭恒晟精密机械有限公司 2025 年度报告",
    "url": "kb://mock/annual.pdf",
    "site_name": "本地知识库",
    "date": "2026-03-20",
    "retrieved_at": "2026-08-22T10:00:00",
    "summary": "云岭恒晟精密机械有限公司主要客户为三家整车厂……",
}


def _admit(claim="公司主要客户为三家整车厂，合计占比约六成", index=1, **over):
    raw = {"claim": claim, "source_result_index": index, "dimension": "operation"}
    raw.update(over)
    return inv.admit_finding(raw, [_SOURCE], "云岭恒晟精密机械有限公司", "2026-08-22")


def test_finding_with_a_resolvable_source_is_admitted():
    finding, reason = _admit()
    assert reason == ""
    assert finding["source"]["published_at"] == "2026-03-20"
    assert finding["source"]["retrieved_at"], "获取时间是硬性要求，不能为空"
    assert finding["verified"] is False, "B 层发现永远是未核实的"


def test_finding_without_a_source_is_dropped():
    """指不到来源的断言就是无源之谈，不得进报告。"""
    _, reason = _admit(index=99)
    assert "未指向可解析的来源" in reason


def test_subject_confirmation_is_decided_by_code_not_by_the_model():
    """模型自报 subject_confirmed=true 不算数。

    同名公司的材料被写成本主体风险，BC-04 / BC-12 / BC-14 都栽过；
    联网之后概率只会更高，判据必须在代码这一侧。
    """
    other = dict(_SOURCE, title="另一家完全无关的公司年度报告",
                 url="kb://mock/other.pdf", summary="另一家公司的经营情况……")
    finding, reason = inv.admit_finding(
        {"claim": "该公司主要客户为三家整车厂", "source_result_index": 1,
         "subject_confirmed": True},          # ← 模型说它确认过了
        [other], "云岭恒晟精密机械有限公司", "2026-08-22")
    assert finding is None
    assert "主体" in reason


def test_source_published_after_the_cutoff_is_dropped():
    """研究截止日在 B 层同样生效（红线 3）。"""
    late = dict(_SOURCE, date="2026-09-01")
    _, reason = inv.admit_finding(
        {"claim": "公司主要客户为三家整车厂", "source_result_index": 1},
        [late], "云岭恒晟精密机械有限公司", "2026-08-22")
    assert "晚于研究截止日" in reason


def test_too_short_a_claim_is_dropped():
    _, reason = _admit(claim="很好")
    assert "过短" in reason


# ------------------------------------------------- 四、失败留痕

def test_failures_distinguish_not_found_from_error():
    """「查了没有」与「没查成」必须可分辨（BC-51）。

    合并成一句"本节无内容"，读者就无法判断该不该补查。
    """
    state = {}
    inv.record_failure(state, "探索性调查", "语料为空", kind="not_found")
    inv.record_failure(state, "探索性调查", "调用超时", kind="error")
    kinds = [f["kind"] for f in state["investigation"]["failures"]]
    assert kinds == ["not_found", "error"]

    state["investigation"]["charts"] = []
    rendered = inv.render_investigation_section(state)
    assert "查了没有" in rendered and "没查成" in rendered


def test_disabled_is_its_own_kind():
    """"没开"与"开了但查空了"是两件事，读者要能分辨。"""
    state = {}
    inv.record_failure(state, "探索性调查", "本次未启用", kind="disabled")
    assert "本次未启用" in inv.render_investigation_section(state)


# ------------------------------------------------- 五、章节收敛

def _state_with_one_chart():
    state = {}
    box = inv.get_investigation(state)
    box["charts"].append(inv.make_chart(
        inv.CHART_DETERMINISTIC, "line", "营业收入趋势", unit="万元",
        series=[{"period": "2024年度", "value": 1.0, "text": "1.0"},
                {"period": "2025年度", "value": 2.0, "text": "2.0"}],
        provenance={"field_id": "revenue", "field_name": "营业收入",
                    "evidence_ids": ["ev_x"]}))
    return state


def test_section_declares_that_it_does_not_participate_in_adjudication():
    """章节抬头必须自己声明它不参与裁决。

    隔离要求 1：B 层内容更好读，混在一起时它会先被采信。
    """
    rendered = inv.render_investigation_section(_state_with_one_chart())
    assert inv.SECTION_MARKER in rendered
    assert "不进入" in rendered and "证据溯源附录" in rendered
    assert rendered.rstrip().endswith(inv.SECTION_END)


def test_empty_investigation_renders_nothing():
    """没有任何内容时不插空章节——空标题比没有标题更让人困惑。"""
    assert inv.render_investigation_section({}) == ""
    assert inv.render_investigation_section({"investigation": inv.empty_investigation()}) == ""


def test_model_rewritten_copies_are_excised_and_replaced():
    """模型改写过的版本一律切除，放回代码生成的权威版本（BC-50 同一策略）。"""
    state = _state_with_one_chart()
    block = inv.render_investigation_section(state)
    tampered = ("正文开头\n\n"
                f"**{inv.SECTION_MARKER}，由系统生成）**\n综合来看该企业经营稳健。\n"
                f"{inv.SECTION_END}\n\n正文结尾")
    out = inv.canonicalize_investigation_section(tampered, block)
    assert "综合来看该企业经营稳健" not in out, "被改写的版本必须整块切除"
    assert "正文开头" in out and "正文结尾" in out
    assert out.count(inv.SECTION_MARKER) == 1


def test_truncated_section_leaves_no_remnant():
    """只找到起始锚点时不保留残块。

    一段被截断的"未经核实"声明比整块丢失危险得多——
    读者会看不到那句声明，却看得到内容。
    """
    truncated = f"正文\n\n**{inv.SECTION_MARKER}，由系统生成）**\n某条未经核实的说法"
    assert "某条未经核实的说法" not in inv.excise_investigation_section(truncated)


# ------------------------------------------------- 六、探索性摄入

def test_ingest_admits_findings_and_records_the_rejected_count():
    """全部被拒与模型返回空数组是两回事，前者说明判据或提示词有问题。"""
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    stats = inv.ingest_exploratory_payload(state, {
        "findings": [
            {"claim": "公司主要客户为三家整车厂，合计占比约六成",
             "source_result_index": 1},
            {"claim": "另一条没有来源的说法，长度足够通过长度校验",
             "source_result_index": None},
        ],
        "relations": [{"source": "云岭恒晟精密机械有限公司", "target": "某整车厂",
                       "relation": "客户", "source_result_index": 1}],
    }, [_SOURCE])
    assert stats["findings"] == 1 and stats["rejected"] == 1
    failures = state["investigation"]["failures"]
    assert failures and "未通过准入判定" in failures[0]["reason"]


def test_ingested_metric_becomes_an_exploratory_chart_with_sources():
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {"metrics": [{
        "name": "在手订单", "unit": "万元", "source_result_index": 1,
        "points": [{"period": "2024年度", "value": 100},
                   {"period": "2025年度", "value": 130}],
    }]}, [_SOURCE])
    charts = state["investigation"]["charts"]
    assert len(charts) == 1
    assert charts[0]["chart_class"] == inv.CHART_EXPLORATORY
    assert charts[0]["provenance"]["sources"][0]["url"] == _SOURCE["url"]


def test_metric_with_one_point_is_not_drawn():
    """探索性图表与确定性图表同一门槛：不足两点不构成趋势。"""
    state = {"company_name": "云岭恒晟精密机械有限公司"}
    inv.ingest_exploratory_payload(state, {"metrics": [{
        "name": "在手订单", "source_result_index": 1,
        "points": [{"period": "2025年度", "value": 130}],
    }]}, [_SOURCE])
    assert state["investigation"]["charts"] == []


def test_exploratory_output_is_bounded():
    """红线 4：B 层要有上界。

    一份带三十张图的尽调报告，读者会放弃逐张核对来源，
    等于所有标注都失效。
    """
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {
        "findings": [{"claim": f"第 {i} 条足够长的可溯源事实陈述内容",
                      "source_result_index": 1} for i in range(60)],
        "metrics": [{"name": f"指标{i}", "source_result_index": 1,
                     "points": [{"period": "2024", "value": 1},
                                {"period": "2025", "value": 2}]}
                    for i in range(20)],
    }, [_SOURCE])
    box = state["investigation"]
    assert len(box["findings"]) <= inv.MAX_FINDINGS
    assert len(box["charts"]) <= inv.MAX_EXPLORATORY_CHARTS


def test_relations_graph_marks_the_subject_and_flags_exploratory():
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {"relations": [
        {"source": "云岭恒晟精密机械有限公司", "target": "某整车厂",
         "relation": "客户", "source_result_index": 1},
        {"source": "某整车厂", "target": "某整车厂",
         "relation": "自环", "source_result_index": 1},        # 自环丢弃
    ]}, [_SOURCE])
    graph = state["investigation"]["graph"]
    assert len(graph["edges"]) == 1
    assert any(n["is_subject"] for n in graph["nodes"])
    assert all(n["exploratory"] for n in graph["nodes"]), \
        "探索性图谱的每个节点都要自带标记，否则前端复制走就丢了"


def test_every_relation_edge_carries_its_source():
    """一条关系边就是一条断言，画成图之后比写成文字更容易被采信。

    隔离要求 2 对它同样成立：每条边都要能指认来源与获取时间。
    """
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {"relations": [
        {"source": "云岭恒晟精密机械有限公司", "target": "某整车厂",
         "relation": "客户", "source_result_index": 1},
    ]}, [_SOURCE])
    edge = state["investigation"]["graph"]["edges"][0]
    assert edge["origin"]["published_at"] == "2026-03-20"
    assert edge["origin"]["retrieved_at"], "获取时间不能为空"


def test_relation_without_a_source_is_rejected_not_drawn():
    """指不到来源的关系不得进图谱，且拒绝要计数。

    让边免检是原实现的一个漏洞：文字发现过闸门，画成图的同一条断言却不过。
    """
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {"relations": [
        {"source": "云岭恒晟精密机械有限公司", "target": "某整车厂",
         "relation": "客户"},                                   # 没给来源
    ]}, [_SOURCE])
    box = state["investigation"]
    assert box["graph"]["edges"] == []
    assert any("关系边" in (f.get("detail") or "") or "未通过准入" in f["reason"]
               for f in box["failures"]), "被拒的关系边必须留痕"


def test_relation_from_another_company_document_is_rejected():
    """主体确认对关系边同样生效。

    同名公司材料里的"A 是 B 的客户"被画进本主体的图谱，
    读者不会去核对那条边来自哪份文档。
    """
    other = dict(_SOURCE, title="另一家完全无关的公司年度报告",
                 url="kb://mock/other.pdf", summary="另一家公司的经营情况……")
    state = {"company_name": "云岭恒晟精密机械有限公司", "as_of": "2026-08-22"}
    inv.ingest_exploratory_payload(state, {"relations": [
        {"source": "某甲公司", "target": "某乙公司",
         "relation": "客户", "source_result_index": 1},
    ]}, [other])
    assert state["investigation"]["graph"]["edges"] == []


# ------------------------------------------------- 七、担保圈图谱

def _guarantee_evidence(credit_code: str):
    """跑真实适配器，按生产的证据记录形状装进证据库。

    刻意用**真实适配器输出**而不是手写一个字典：手写的会固化我对
    `as_graph()` 形状的假设，那份假设一旦与实现分叉，测试仍然全绿。
    """
    from service.datasource.mock.guarantee_circle import GuaranteeCircleAdapter
    result = GuaranteeCircleAdapter().fetch({"credit_code": credit_code})
    if result is None:
        return None
    return {"ev_g1": {"evidence_id": "ev_g1", "field_id": "guarantee_circle",
                      "source_adapter": "graph_analysis", "active": True,
                      "raw": result.raw}}


def _registry_subject_with_circle():
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "app", "data",
                        "sources", "relation_registry.json")
    with open(path, encoding="utf-8") as fh:
        records = json.load(fh)["companies"]
    from service.guarantee_graph import GuaranteeGraph, detect_guarantee_circles
    graph = GuaranteeGraph.from_records(records)
    for code, record in records.items():
        report = detect_guarantee_circles(record["company_name"], graph)
        if report.has_circle:
            return code, record["company_name"]
    raise AssertionError("关联关系库里没有任何担保圈，无法验证该图谱")


def test_guarantee_graph_chart_comes_from_the_adapter_evidence():
    """担保圈图谱是确定性图谱的第一个实例。

    评审会问"凭什么说它涉入担保圈"，答案必须是图上每条边的金额与期间，
    并挂在一个 evidence_id 上——不是"模型认为"。
    """
    code, name = _registry_subject_with_circle()
    store = _guarantee_evidence(code)
    assert store is not None, f"{name} 应能推导出担保关系"

    chart = inv.build_guarantee_graph_chart({"evidence_store": store})
    assert chart is not None
    assert chart["chart_class"] == inv.CHART_DETERMINISTIC
    assert chart["provenance"]["evidence_ids"] == ["ev_g1"], "图谱必须挂在证据编号上"
    assert chart["graph"]["edges"], "有环的主体必须画得出边"
    assert any(e.get("on_circle") for e in chart["graph"]["edges"]),         "环上的边要带标记，否则前端无法把它标红"
    assert "担保环" in (chart["subtitle"] or "")


def test_no_graph_evidence_yields_no_chart_rather_than_an_empty_one():
    """取不到证据就不画。

    一张空的担保图会被读成"没有担保关系"，而实际含义是"这次没查"——
    这正是整个系统在防的那件事。
    """
    assert inv.build_guarantee_graph_chart({"evidence_store": {}}) is None


def test_superseded_evidence_is_not_drawn():
    """只画当前生效的证据。

    被取代的旧证据仍留在证据库里（BC-38 的 active/superseded 设计），
    拿它画图等于把已被推翻的结论重新摆到读者面前。
    """
    code, _ = _registry_subject_with_circle()
    store = _guarantee_evidence(code)
    store["ev_g1"]["active"] = False
    assert inv.build_guarantee_graph_chart({"evidence_store": store}) is None


# ------------------------------------------------- 八、不得重复清单字段

#: 2026-08-22 带语料的真实运行里，模型交出的**全部** 6 条发现。
#: 它们每一条都是清单字段的复述——而当时提示词里写着「写了也会被丢弃」，
#: 代码里却没有任何地方执行那句话（BC-73）。
#:
#: 用真实输出而不是我编的例子做夹具：合成用例只能覆盖我想到的形态，
#: 而这一批恰恰是我没想到模型会写的那种。
REAL_DUPLICATE_CLAIMS = [
    "云岭恒晟精密机械有限公司的法定代表人郑允升",
    "云岭恒晟精密机械有限公司注册地址为云岭市高新区科苑北路17号",
    "云岭恒晟精密机械有限公司成立日期为2011年09月06日",
    "云岭恒晟精密机械有限公司注册资本为8000万元人民币",
    "云岭恒晟精密机械有限公司登记状态为存续",
    "云岭恒晟精密机械有限公司统一社会信用代码为91990099MA9XFICT02",
]

#: 清单覆盖不到、正是调查层该补的东西。**必须放行**——
#: 只钉"该拒的拒了"，很容易写出一个把所有东西都拒掉的判据。
LEGITIMATE_CLAIMS = [
    "公司主要客户为三家整车厂，2025 年合计占营业收入约六成",
    "在建的二期精密铸造车间预计 2026 年下半年投产，设计产能提升四成",
    "报告期内新增两家海外经销商，出口收入占比由 8% 升至 15%",
    "上游主要原材料为特种合金钢，采购价格随钢材期货波动明显",
    "公司与主要供应商签订年度框架协议，账期为发货后 90 天",
]

SUBJECT = "云岭恒晟精密机械有限公司"


def _verified_checks():
    """真实档案的已核实清单——复述判定要拿真取值比对，不能用占位串。"""
    from config.dd_checklist import build_field_checks
    from service.company_profile import (
        fill_field_checks, find_company, profile_to_facts,
    )
    company = find_company("对云岭恒晟精密机械有限公司开展应收账款保理尽职调查")
    assert company is not None, "测试档案 MOCK-002 缺失"
    checks = build_field_checks(scenario="factoring")
    fill_field_checks(company, profile_to_facts(company), checks)
    return checks


def test_every_real_duplicate_claim_is_rejected():
    """那一轮模型交出的 6 条，一条都不得进报告。

    后果不是"多了几条废话"：同一份报告里同一批事实出现两次、
    挂着相反的可信度标签——信用代码在 A 层标「已核实」，
    在 B 层标「未经核实」。这比没有 B 层更糟。
    """
    checks = _verified_checks()
    for claim in REAL_DUPLICATE_CLAIMS:
        reason = inv.restates_checklist_field(claim, checks, SUBJECT)
        assert reason, f"漏放了清单字段的复述：{claim}"


def test_legitimate_investigation_claims_still_pass():
    """反面：清单覆盖不到的东西必须放行。

    「主要客户为三家整车厂，合计占营业收入约六成」提到了"营业收入"，
    但去掉之后仍有大量自有信息——判据不能因为蹭到一个术语就把它拦掉，
    那正是调查层存在的理由。
    """
    checks = _verified_checks()
    for claim in LEGITIMATE_CLAIMS:
        reason = inv.restates_checklist_field(claim, checks, SUBJECT)
        assert not reason, f"误伤了合法发现：{claim}｜{reason}"


def test_the_gate_runs_inside_admission():
    """判据必须真的接在准入上——单独存在一个正确的函数不算数。

    这是本项目第 N 次「造好了但没接到执行路径上」的预防：
    走 `ingest_exploratory_payload` 这个真实入口，而不是直接调判据。
    """
    state = {"company_name": SUBJECT, "as_of": "2026-08-22",
             "field_checks": _verified_checks()}
    stats = inv.ingest_exploratory_payload(state, {
        "findings": [{"claim": c, "source_result_index": 1}
                     for c in REAL_DUPLICATE_CLAIMS],
    }, [_SOURCE])
    assert stats["findings"] == 0, "清单字段的复述仍然进了报告"
    assert stats["rejected"] == len(REAL_DUPLICATE_CLAIMS)
    assert state["investigation"]["findings"] == []


def test_rejections_are_visible_so_an_over_tight_gate_can_be_caught():
    """拒绝要计数留痕。

    判据太严会把调查层整个掏空，而那**看起来和"材料里没东西"一模一样**。
    只有拒绝数可见，才能事后判断是资料的问题还是判据的问题。
    """
    state = {"company_name": SUBJECT, "as_of": "2026-08-22",
             "field_checks": _verified_checks()}
    inv.ingest_exploratory_payload(state, {
        "findings": [{"claim": REAL_DUPLICATE_CLAIMS[0],
                      "source_result_index": 1}],
    }, [_SOURCE])
    failures = state["investigation"]["failures"]
    assert failures and "未通过准入判定" in failures[0]["reason"]
    assert "清单" in (failures[0].get("detail") or ""), \
        "留痕里要写明是被哪一类判据拒的"


def test_relation_edges_are_not_subject_to_the_restatement_gate():
    """关系边走另一条语义，不做复述判定。

    「A 与 B 存在客户关系」不是对某个清单字段取值的复述；
    把它塞进同一道判据只会产生难以解释的误伤。
    """
    state = {"company_name": SUBJECT, "as_of": "2026-08-22",
             "field_checks": _verified_checks()}
    inv.ingest_exploratory_payload(state, {"relations": [
        {"source": SUBJECT, "target": "某整车厂",
         "relation": "客户", "source_result_index": 1},
    ]}, [_SOURCE])
    assert len(state["investigation"]["graph"]["edges"]) == 1


def test_vocabulary_is_derived_from_the_checklist_not_hand_written():
    """术语表必须由清单派生。

    手写一份会与清单分叉：新增字段时没人记得回来补，
    于是判据对新字段静默失效——而失效看起来和"模型没写那类内容"一样。
    """
    from config.dd_checklist import CHECKLIST
    vocabulary = set(inv._checklist_vocabulary())
    for item in CHECKLIST:
        assert item.field_name in vocabulary, \
            f"清单项 {item.field_name} 不在术语表里，判据对它无效"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
