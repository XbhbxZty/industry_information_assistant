# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
稳定核心 + 确定性场景扩展（BC-58）

## 这一轮在钉什么

固定二十项被当成了所有业务场景的全集。case_01（应收账款保理）的 29 条决策
参考主张里，只有 2 条的字段名落在这二十项内——应收账款账龄、客户集中度、
存货、资本开支、海外收入、审计意见、内控缺陷全都没有对应字段。
换模型不可能生成一个系统 schema 里不存在的字段。

## 为什么不是"让模型自己加字段"

每条打分规则都是 `risk_scorecard._ok(field_id)` 的硬编码查表。模型临时发明的
字段进不了打分、完整度、闸门与额度，只能是报告里的装饰性文字——而缺的是
决策变量，不是报告字数。所以扩展项由**代码**定义，由 `business_type` 确定性选出。

## 断言分三层

1. **扩展是加法**：启用场景后，核心二十项的核实率、维度率、必查项数
   **一个都不能变**。加字段顺手改判据，等于静默移动一套已标定的闸门。
2. **分层不可绕过**：场景项不得进核心指标，也不得触发任何评分规则。
3. **场景选择是确定性的**：精确别名匹配，不做模糊包含；猜错场景会给
   信息缺口清单凭空加十几条噪声。

运行：cd backend && python -m pytest tests/test_scenario_checklist.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config import dd_checklist  # noqa: E402
from config.dd_checklist import (  # noqa: E402
    ALL_SCENARIO_IDS, CHECKLIST, CHECKLIST_BY_ID, CORE_IDS, REQUIRED_IDS,
    SCENARIO_CHECKLISTS, build_field_checks, compute_completeness, is_core_check,
    resolve_scenario, scenario_checklist,
)
from service.rag_evidence_bridge import _FIELD_TERMS, _verbatim_compact  # noqa: E402
from service.risk_scorecard import PROFILE_BACKED_FIELDS, score  # noqa: E402


# --------------------------------------------------- 一、扩展必须是纯加法

def test_core_checklist_still_has_exactly_twenty_items():
    assert len(CHECKLIST) == 20
    assert len(CORE_IDS) == 20
    assert len(REQUIRED_IDS) == 15, "必查项数是完整度闸门的分母，不得因扩展而变化"


def test_enabling_a_scenario_does_not_move_any_core_metric():
    """同一份核心清单，启用场景前后核心指标必须逐字相同。"""
    core_only = build_field_checks(checked_at="2026-08-17")
    with_scenario = build_field_checks(checked_at="2026-08-17", scenario="factoring")

    before, after = compute_completeness(core_only), compute_completeness(with_scenario)
    for key in ("required_total", "required_verified", "verified_rate",
                "unverified_fields", "capability_gaps", "by_category"):
        assert before[key] == after[key], f"{key} 因启用场景而改变——扩展不是加法了"


def test_scenario_items_are_reported_separately_not_folded_into_core():
    checks = build_field_checks(checked_at="2026-08-17", scenario="factoring")
    completeness = compute_completeness(checks)
    assert completeness["scenario"]["name"] == "factoring"
    assert completeness["scenario"]["total"] == len(SCENARIO_CHECKLISTS["factoring"])
    assert completeness["required_total"] == 15, "场景项绝不能进必查分母"
    for field_id in ALL_SCENARIO_IDS:
        assert field_id not in completeness["by_category"].get("financial", {}).get("ids", []), \
            "场景项不得进维度统计"


def test_core_only_run_reports_an_empty_scenario_block():
    completeness = compute_completeness(build_field_checks(checked_at="2026-08-17"))
    assert completeness["scenario"]["total"] == 0
    assert completeness["scenario"]["rate"] == 0.0
    assert completeness["scenario"]["name"] == ""


# ------------------------------------------- 二、分层不可绕过（决策侧隔离）

def test_scenario_fields_trigger_no_scoring_rule_even_when_verified():
    """场景项被核实也不得改变评分。

    评分规则集是 `_ok(field_id)` 的硬编码查表，场景项不在其中。这一条钉住
    "扩展不影响评级"这个承诺——否则加一批字段就会悄悄改变授信结论。
    """
    company = {"name": "测试主体", "registration": {}, "coverage": {"queried": []}}
    core = build_field_checks(checked_at="2026-08-17")
    extended = build_field_checks(checked_at="2026-08-17", scenario="factoring")
    for check in extended:
        if not is_core_check(check):
            check["status"] = "verified"
            check["value"] = "1,234"

    baseline = score(company, core, compute_completeness(core))
    with_scenario = score(company, extended, compute_completeness(extended))
    assert baseline["level"] == with_scenario["level"]
    assert baseline["composite_score"] == with_scenario["composite_score"]
    assert baseline["triggered_rules"] == with_scenario["triggered_rules"]


def test_no_scenario_field_is_registered_as_profile_backed():
    """场景项不得登记进 PROFILE_BACKED_FIELDS。

    该集合的含义是"这个字段必须能合并进档案，否则不予评级"。场景项没有
    投影器，登记进去只会在投影环节抛错（BC-31 的反面）。
    """
    assert not (ALL_SCENARIO_IDS & PROFILE_BACKED_FIELDS)


def test_scenario_ids_never_collide_with_core_ids():
    assert not (ALL_SCENARIO_IDS & CORE_IDS)
    assert set(CHECKLIST_BY_ID) == CORE_IDS | ALL_SCENARIO_IDS


def test_every_checklist_field_has_deterministic_keywords():
    """每个字段都要有确定性关键词，否则证据闸门会 KeyError。

    这一条防的是"加了字段忘了加关键词"——那会让新字段在运行时直接崩掉，
    而不是优雅地无法核实。
    """
    missing = sorted(set(CHECKLIST_BY_ID) - set(_FIELD_TERMS))
    assert not missing, f"这些字段缺少 _FIELD_TERMS 条目：{missing}"


def test_no_checklist_keyword_set_is_dead_on_real_corpus():
    """每个字段的关键词集至少要能在真实语料上命中一次（BC-60）。

    改造前 `_field_is_complete_enough` 用 `前10名股东` 判定股东结构完整，
    而这个措辞在 case_01 全语料里出现 **0** 次——该项永远不可能被判定完整，
    且完全静默：failure_reason 写"仅命中个别持股事实"，读起来像一次正常缺口。

    一条永远不满足的判据不会报错、不会崩溃，产出与"这次确实没查到"完全一致。
    能提前发现它的只有拿真实语料做一次可满足性检查。
    """
    import json

    corpus = os.path.join(os.path.dirname(__file__), "..", "eval",
                          "real_cases_processed", "case_01", "corpus", "chunks.jsonl")
    if not os.path.exists(corpus):
        return          # 语料未落盘时跳过，不伪造通过
    with open(corpus, encoding="utf-8") as handle:
        blob = "".join(json.loads(line)["content"] for line in handle if line.strip())
    compact_blob = _verbatim_compact(blob)

    dead = [
        field_id for field_id, terms in _FIELD_TERMS.items()
        if not any(_verbatim_compact(term) in compact_blob for term in terms)
    ]
    # 工商、司法、舆情类字段在这份上市公司语料里本就可能整类缺席，
    # 只断言财务与保理场景字段——这些是本案例真实存在的内容。
    financial_ish = {
        "revenue", "net_profit", "cash_flow", "debt_ratio", "shareholders",
        "actual_controller", "related_party",
    } | set(ALL_SCENARIO_IDS)
    assert not (set(dead) & financial_ish), (
        f"这些字段的关键词在真实语料上一次都命中不了，等于永久失明："
        f"{sorted(set(dead) & financial_ish)}"
    )


def test_completeness_terms_tolerate_pdf_inserted_whitespace():
    """完整度判据的匹配口径必须与字段关键词闸门一致（BC-60 的第二个入口）。

    PDF 抽取会在词内插入空白：`1 年以内` 在语料里出现 11 次，`1年以内` 出现
    0 次。字段关键词闸门走 `_verbatim_compact` 不受影响，而完整度判据原来用
    裸 `in`——同一份原文，两处判据给出不同答案，不一致的那一处让该项永远不完整。
    """
    from service.rag_evidence_bridge import _field_is_complete_enough

    spaced = "前十 名股东 情况\n股东 名称 持股比例\n宁德时代新能源 12.34%"
    complete, reason = _field_is_complete_enough("shareholders", [{"quote": spaced}])
    assert complete, f"排版空白不应让完整度判据失配：{reason}"

    # 反面：措辞真的不在原文里时，仍必须判为不完整。
    partial = "某股东持股 5.00%"
    complete, reason = _field_is_complete_enough("shareholders", [{"quote": partial}])
    assert not complete and "个别持股事实" in reason


def test_legacy_checks_without_scope_are_treated_as_core():
    """老检查点里的 check 没有 scope 字段。

    身份要建在不可变的键（field_id）上，不能靠 scope 的缺省值判定——
    否则历史数据会被整体误判成场景项，核实率凭空归零（BC-53 同一条纪律）。
    """
    legacy = [{"field_id": "revenue", "required": True, "status": "verified",
               "category": "financial"}]
    assert is_core_check(legacy[0])
    assert compute_completeness(legacy)["required_verified"] == 1


# --------------------------------------------- 三、场景选择必须是确定性的

def test_scenario_resolution_is_exact_alias_matching_only():
    assert resolve_scenario("factoring") == "factoring"
    assert resolve_scenario("应收账款保理") == "factoring"
    assert resolve_scenario("Factoring") == "factoring", "大小写不敏感是别名规范化"
    assert resolve_scenario(None, "", "保理") == "factoring", "多个线索取第一个命中的"


def test_unknown_business_type_falls_back_to_core_only():
    assert resolve_scenario("corporate_credit") == ""
    assert resolve_scenario("某种没见过的业务") == ""
    assert resolve_scenario("保理业务咨询与培训服务") == "", \
        "模糊包含会把无关业务误判成保理，给缺口清单凭空加十几条噪声"
    assert scenario_checklist("") == []
    assert len(build_field_checks(checked_at="t", scenario="")) == 20


def test_duplicate_scenario_id_is_rejected_at_definition_time():
    """重名守卫必须真的会炸。

    场景项与核心项重名会让核心闸门读到场景数据——这属于必须当场失败的
    编程错误，不能等到跑分时才发现。
    """
    from config.dd_checklist import _item, index_checklists

    clash = _item("revenue", "重名项", "financial", False, "d", "financial_report")
    try:
        index_checklists(CHECKLIST, {"bogus": [clash]})
    except ValueError as exc:
        assert "重名" in str(exc)
    else:
        raise AssertionError("与核心项重名的场景字段必须被拒绝")

    # 两个场景之间重名同样不允许：同一个 id 在不同场景下含义可能不同，
    # 而证据链只有一张索引。
    other = _item("shared_id", "甲场景项", "financial", False, "d", "financial_report")
    try:
        index_checklists(CHECKLIST, {"a": [other], "b": [other]})
    except ValueError as exc:
        assert "重名" in str(exc)
    else:
        raise AssertionError("跨场景重名也必须被拒绝")


def test_real_definition_passes_the_collision_guard():
    """正面：真实定义必须能通过守卫，否则守卫写反了也测不出来。"""
    from config.dd_checklist import index_checklists

    index = index_checklists(CHECKLIST, SCENARIO_CHECKLISTS)
    assert set(index) == CORE_IDS | ALL_SCENARIO_IDS


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {str(e)[:170]}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} 通过")
    sys.exit(1 if failed else 0)
