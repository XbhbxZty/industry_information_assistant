# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
跨层一致性判定（双轨产出计划阶段 2）

## 这一轮在钉什么

A 层出等级，B 层出发现，判定层回答**那些发现是否构成对 A 层的实质挑战**。

计划第四节否掉了"两层各自打分再比对"：A 层结构上只有抬升下限的闸门、
几乎没有降低等级的路径，B 层从自由调查出发倾向给低风险。硬要两者输出
同一个等级，**比出来的差异大部分是口径差异而非判断差异**。

所以钉的不是"能判出不一致"，而是**判出来的不一致确实是判断差异**：

1. 指向**已核实**字段才算矛盾——清单没结论就没有可被推翻的东西
2. 正面发现永远不单独触发（否则几条好新闻就能把高风险降下来）
3. 模型自报的取值只是输入，非法值收敛为 unknown 而**不是就近纠正**
4. 阶段 2 只观察：**判定不得触发人工复核、不得改动等级与额度**

运行：cd backend && python -m pytest tests/test_cross_layer_verdict.py -q
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks  # noqa: E402
from service import cross_layer_verdict as clv  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _checks():
    company = find_company("对云岭恒晟精密机械有限公司开展应收账款保理尽职调查")
    assert company is not None, "测试档案 MOCK-002 缺失"
    checks = build_field_checks(scenario="factoring")
    fill_field_checks(company, profile_to_facts(company), checks)
    return checks


#: 该主体已核实为「经查询，无相关记录」的字段——**唯一**能被代码判定
#: 为"被推翻"的形态。选它而不是 litigation：后者的取值是具体案号，
#: 与材料的比对属于 undecidable，测不出规则 1。
NEGATIVE_FIELD = "enforcement"


def _finding(**over):
    base = {
        "claim": "媒体报道公司新增一起 3200 万元被执行案件，执行标的尚未清偿",
        "dimension": "judicial", "direction": "aggravating",
        "materiality": "high", "subject_confirmed": True,
        "field_id": "", "source": {"publisher": "本地知识库",
                                   "published_at": "2026-03-20"},
    }
    base.update(over)
    return base


def _judge(*findings, checks=None):
    """按**生产路径**给发现打上冲突戳，再交给判定。

    路 B 之后 `judge_findings` 只消费 `finding["contradiction"]`，
    而那个戳是准入时由 `detect_contradiction` 打的。夹具跳过打戳，
    测出来的就是另一个系统（这一点在 BC-72 的用例上栽过一次）。
    """
    checks = checks if checks is not None else _checks()
    stamped = [{**f, "contradiction": clv.detect_contradiction(f, checks)}
               for f in findings]
    return clv.judge_findings(stamped, checks)


# ------------------------------------------------- 一、规则 1：与已核实值矛盾

def test_contradicting_a_verified_field_is_a_challenge():
    """最强信号：清单说「经查询，无相关记录」，材料里却有一条。"""
    assert any(c["field_id"] == NEGATIVE_FIELD and c["status"] == "verified"
               for c in _checks()), "前提：该项是一条已核实的否定结论"
    out = _judge(_finding(field_id=NEGATIVE_FIELD))
    assert out["verdict"] == "challenged"
    assert out["counts"][clv.RULE_CONTRADICTS_VERIFIED] == 1


def test_the_challenge_cites_the_a_layer_value():
    """阶段 2 的验收要求「每条都能指认具体依据」。

    只说"存在不一致"没有用——复核人要能当场看到清单那一项写的是什么。
    """
    out = _judge(_finding(field_id=NEGATIVE_FIELD))
    item = out["challenges"][0]
    assert item["field_id"] == NEGATIVE_FIELD
    assert item["a_layer_value"], "没带上 A 层取值，无法指认依据"
    assert NEGATIVE_FIELD in item["why"]
    assert item["source"]["publisher"] == "本地知识库"


def test_contradicting_an_unverified_field_is_not_a_challenge():
    """**这是最要紧的一条边界。**

    清单在那一项上没有结论，也就没有可被推翻的东西——那是 B 层在填空缺，
    与"清单说 A、调查说 B"完全不同。混为一谈会让不一致率虚高，
    而阶段 2 的全部目的就是测准这个率。
    """
    out = _judge(_finding(field_id="accounts_receivable_aging"))
    assert out["verdict"] == "consistent"
    assert out["counts"][clv.NOTE_FILLS_GAP] == 1
    assert out["notes"][0]["note"] == clv.NOTE_FILLS_GAP


def test_contradicting_a_nonexistent_field_is_recorded_not_believed():
    """模型编了一个不存在的字段名，不得当作矛盾。

    采信它等于让模型凭空创造一条对清单的挑战。
    """
    out = _judge(_finding(field_id="没有这个字段"))
    assert out["verdict"] == "consistent"
    assert out["counts"][clv.NOTE_INVALID_FIELD] == 1


# ------------------------------------------------- 二、规则 2：未覆盖维度

def _uncovered_dimension(checks):
    covered = clv.covered_dimensions(checks)
    for dim in clv.DIMENSIONS:
        if dim not in covered:
            return dim
    return ""


def test_high_aggravating_finding_in_an_uncovered_dimension_is_a_challenge():
    """B 层在 A 层完全没看见的地方发现了重要负面事实。"""
    checks = _checks()
    dim = _uncovered_dimension(checks)
    if not dim:
        # 该档案每个维度都有已核实项——构造一个空覆盖来验规则本身
        checks = [dict(c, status="unverified") for c in checks]
        dim = "judicial"
    out = clv.judge_findings([_finding(dimension=dim)], checks)
    assert out["counts"][clv.RULE_HIGH_AGGRAVATING_UNCOVERED] == 1


def test_a_covered_dimension_does_not_trigger_rule_two():
    """A 层在该维度已有已核实结论时，不算"没看见"。

    否则每一条负面发现都会触发，规则 2 就退化成"只要是负面就不一致"。
    """
    checks = _checks()
    covered = clv.covered_dimensions(checks)
    assert covered, "前提：档案里至少有一个维度已被覆盖"
    dim = sorted(covered & set(clv.DIMENSIONS))[0]
    out = clv.judge_findings([_finding(dimension=dim)], checks)
    assert out["counts"][clv.RULE_HIGH_AGGRAVATING_UNCOVERED] == 0


def test_unconfirmed_subject_does_not_trigger_rule_two():
    """主体没确认的发现不得挑战本主体的结论（红线 3）。"""
    checks = [dict(c, status="unverified") for c in _checks()]
    out = clv.judge_findings(
        [_finding(dimension="judicial", subject_confirmed=False)], checks)
    assert out["counts"][clv.RULE_HIGH_AGGRAVATING_UNCOVERED] == 0


def test_medium_materiality_does_not_trigger_rule_two():
    checks = [dict(c, status="unverified") for c in _checks()]
    out = clv.judge_findings(
        [_finding(dimension="judicial", materiality="medium")], checks)
    assert out["counts"][clv.RULE_HIGH_AGGRAVATING_UNCOVERED] == 0


# ------------------------------------------------- 三、规则 3：红线

def test_mitigating_never_triggers_a_challenge_on_its_own():
    """**红线**：正面发现永远不单独触发。

    否则搜到几条正面报道就能把高风险降下来——
    那是 BC-P3「数据缺失判低风险」的变体。
    """
    checks = [dict(c, status="unverified") for c in _checks()]
    out = clv.judge_findings([
        _finding(dimension="judicial", direction="mitigating",
                 claim="行业协会通报公司位列区域出货量前三"),
        _finding(dimension="operation", direction="mitigating",
                 claim="公司获评省级专精特新中小企业"),
    ], checks)
    assert out["verdict"] == "consistent"
    assert out["counts"]["mitigating_suppressed"] == 2
    assert out["challenges"] == []


def test_a_mitigating_finding_may_still_contradict_a_verified_field():
    """规则 3 压制的是"单独触发"，不是"一律忽略"。

    正面发现若明确与已核实结论矛盾（清单说有被执行、调查说已结清），
    那仍然是一条需要人看的矛盾——只是它不会因为"正面"就自动降级。
    """
    out = _judge(_finding(direction="mitigating", field_id=NEGATIVE_FIELD))
    assert out["counts"][clv.RULE_CONTRADICTS_VERIFIED] == 1


# ------------------------------------------------- 四、模型自报值的收敛

def test_illegal_values_become_unknown_rather_than_a_nearby_valid_one():
    """"模型写了个我们不认识的词"和"模型说它是高风险"是两回事。

    就近纠正等于拿"没说"冒充"说了"。
    """
    out = clv.normalize_judgment(
        {"direction": "严重", "materiality": "极高", "dimension": "财务"})
    assert out == {"direction": clv.UNKNOWN, "materiality": clv.UNKNOWN,
                   "dimension": clv.UNKNOWN}


def test_unknown_direction_never_triggers_rule_two():
    """方向未知时不得按 aggravating 处理——那会让缺字段的候选凭空触发。"""
    checks = [dict(c, status="unverified") for c in _checks()]
    out = clv.judge_findings(
        [_finding(dimension="judicial", direction="不知道")], checks)
    assert out["counts"][clv.RULE_HIGH_AGGRAVATING_UNCOVERED] == 0


# ------------------------------------------------- 五、阶段 2 只观察

def test_the_verdict_never_triggers_human_review():
    """阶段 2 的核心约束：只告警不闸门。"""
    out = _judge(_finding(field_id=NEGATIVE_FIELD))
    assert out["verdict"] == "challenged"
    assert out["triggers_human_review"] is False


def test_the_module_never_writes_any_adjudication_key():
    """判定层不得写 A 层任何字段。

    走 AST 而不是字符串匹配——这个项目已五次被"守卫误伤"咬到。
    """
    with open(os.path.join(APP, "service/cross_layer_verdict.py"),
              encoding="utf-8") as fh:
        module = ast.parse(fh.read())
    protected = {"risk_assessment", "credit_recommendation", "field_checks",
                 "completeness", "requires_human_review", "level"}
    written = set()
    for node in ast.walk(module):
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else [])
        for target in targets:
            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                if isinstance(target.slice.value, str):
                    written.add(target.slice.value)
    assert not (written & protected), f"判定层写了 A 层键：{sorted(written & protected)}"


def test_human_review_flag_is_a_literal_false_not_a_computed_value():
    """`triggers_human_review` 必须是写死的 False。

    写死是刻意的：让"升级成闸门"成为一次**显式的决定**，
    而不是某次改动里一个表达式悄悄变成了 True。
    """
    with open(os.path.join(APP, "service/cross_layer_verdict.py"),
              encoding="utf-8") as fh:
        module = ast.parse(fh.read())
    found = []
    for node in ast.walk(module):
        if (isinstance(node, ast.Dict)):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant)
                        and key.value == "triggers_human_review"):
                    found.append(value)
    assert found, "没找到 triggers_human_review 的赋值"
    for value in found:
        assert isinstance(value, ast.Constant) and value.value is False, \
            "阶段 2 里它必须是字面量 False"


# ------------------------------------------------- 六、可导出

def test_the_record_is_exportable_and_self_contained():
    """验收要求：连续运行的不一致记录**可导出**，每条能指认依据。

    "可导出"意味着这条记录离开本次运行的上下文之后仍然读得懂——
    所以规则名、陈述、依据、来源必须都在记录里，不能靠外部拼装。
    """
    import json
    out = _judge(_finding(field_id=NEGATIVE_FIELD),
                 _finding(direction="mitigating"))
    dumped = json.loads(json.dumps(out, ensure_ascii=False, default=str))
    assert dumped["counts"]["findings"] == 2
    item = dumped["challenges"][0]
    for key in ("rule", "claim", "why", "source", "field_id", "a_layer_value"):
        assert key in item, f"导出记录缺少 {key}"


def test_empty_findings_produce_a_consistent_verdict_not_a_missing_one():
    """没有发现时也要给出判定。

    缺一个判定和"判定为一致"在聚合统计里含义完全不同——
    前者会让那一轮从分母里消失，把不一致率算高。
    """
    out = clv.judge_findings([], _checks())
    assert out["verdict"] == "consistent"
    assert out["counts"]["findings"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
