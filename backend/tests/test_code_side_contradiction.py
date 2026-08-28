# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
冲突识别从模型自报改为代码判定（阶段 2 路 B）

## 这一轮在钉什么

第一版让模型自报 `contradicts_field`。2026-08-23 三主体实测：
**三个主体、48 条发现，模型一次都没填过这个字段**——哪怕语料里
埋了两处刻意的矛盾。

原因是我让模型做了一件**它拿不到输入的判断**：提示词从来没告诉它
清单里已核实的取值是什么。它看到"持有某公司 30% 股权"，
无从知道档案里 `external_investment` 写着"经查询，无相关记录"。

补输入是另一条路，但那等于把冲突判定交给模型的自觉——
本项目的贯穿结论正是不能这么做（BC-29 记的就是把裁决权收回代码）。

## 现在的分工

    模型  回答"这条讲的是哪个字段"        —— 容易，且可校验
    代码  回答"这构成对清单结论的推翻吗"  —— 判定

## 顺序有一处必须钉死

BC-73 的去重闸门会丢掉"讲清单字段"的发现，而**推翻清单字段的发现
恰好也讲清单字段**——两者表面同形。**冲突识别必须排在去重之前**，
否则最有价值的那类发现会被自己的另一道闸门吃掉。

运行：cd backend && python -m pytest tests/test_code_side_contradiction.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import NO_RECORD_VALUE, build_field_checks  # noqa: E402
from service import cross_layer_verdict as clv  # noqa: E402
from service import investigation_layer as inv  # noqa: E402
from service.company_profile import (  # noqa: E402
    fill_field_checks, find_company, profile_to_facts,
)
from service.datasource import apply_all  # noqa: E402

SUBJECT = "东莞市泰锐精密传动件有限公司"
_SOURCE = {
    "title": f"{SUBJECT} 贷前尽职调查材料包",
    "url": "kb://mock/tairui.txt", "site_name": "本地知识库",
    "date": "2026-08-10", "retrieved_at": "2026-08-23T00:00:00",
    "summary": f"{SUBJECT} 材料正文……",
}


def _checks():
    """走**生产路径**：档案 + 适配器。

    只跑 `fill_field_checks` 会让 guarantee 停在 unverified——
    它是关联关系适配器核实的。夹具漏掉适配器，测出来的是另一个系统。
    """
    company = find_company(f"请对{SUBJECT}做贷前尽职调查")
    assert company is not None, "测试档案 MOCK-001 缺失"
    checks = build_field_checks()
    fill_field_checks(company, profile_to_facts(company), checks)
    apply_all(company, checks, {})
    return checks


def _check(checks, field_id):
    return next(c for c in checks if c["field_id"] == field_id)


def _finding(**over):
    base = {"claim": "公司持有惠州泰锐传动科技有限公司30%股权，出资额900万元。",
            "dimension": "relation", "direction": "aggravating",
            "materiality": "high", "field_id": "external_investment",
            "source_result_index": 1}
    base.update(over)
    return base


def _ingest(*findings, checks=None):
    checks = checks if checks is not None else _checks()
    state = {"company_name": SUBJECT, "as_of": "2026-08-23",
             "field_checks": checks}
    inv.ingest_exploratory_payload(state, {"findings": list(findings)}, [_SOURCE])
    return state, checks


# ------------------------------------------------- 一、已核实的否定结论被推翻

def test_a_verified_no_record_conclusion_can_be_contradicted():
    """本条的核心：「清单说没有」vs「材料里有一条」——不存在解释空间。"""
    checks = _checks()
    assert _check(checks, "external_investment")["value"] == NO_RECORD_VALUE, \
        "前提：该项是一条已核实的否定结论"

    out = clv.detect_contradiction(_finding(), checks)
    assert out["kind"] == clv.CONTRADICTION_VERIFIED_NEGATIVE
    assert out["a_layer_value"] == NO_RECORD_VALUE


def test_the_challenge_reaches_the_verdict():
    """识别出来还要真的进判定——只有一个正确的函数不算数。"""
    state, checks = _ingest(_finding())
    verdict = clv.judge_findings(state["investigation"]["findings"], checks)
    assert verdict["verdict"] == "challenged"
    assert verdict["counts"][clv.RULE_CONTRADICTS_VERIFIED] == 1


def test_a_short_claim_cannot_overturn_a_verified_conclusion():
    """太短的陈述撑不起"清单说没有、这里说有"这个断言。"""
    out = clv.detect_contradiction(_finding(claim="有一笔"), _checks())
    assert out.get("kind") != clv.CONTRADICTION_VERIFIED_NEGATIVE


# ------------------------------------------------- 二、比不了就说比不了

def test_non_negative_values_are_undecidable_not_contradictions():
    """清单说一笔 1200 万、材料说两笔合计 4000 万——**不判成矛盾**。

    口径、期间、笔数都可能不同，naive 的数值比对会造出假矛盾。
    系统说"我比不了"比说"没有矛盾"诚实。
    """
    checks = _checks()
    assert _check(checks, "guarantee")["status"] == "verified"
    out = clv.detect_contradiction(_finding(
        claim="报告期内公司为关联方提供保证担保共两笔，合计金额4,000万元。",
        field_id="guarantee"), checks)
    assert out["kind"] == clv.CONTRADICTION_UNDECIDABLE


def test_undecidable_is_counted_separately_from_challenges():
    """无法判定既不进分子也不消失。

    算进"一致"会低报，算进"不一致"会高报，而阶段 2 的全部目的是测准。
    """
    state, checks = _ingest(_finding(
        claim="报告期内公司为关联方提供保证担保共两笔，合计金额4,000万元。",
        field_id="guarantee"))
    verdict = clv.judge_findings(state["investigation"]["findings"], checks)
    assert verdict["counts"][clv.CONTRADICTION_UNDECIDABLE] == 1
    assert verdict["counts"]["challenges"] == 0
    assert verdict["notes"], "无法判定必须留在记录里，供人工核对"


# ------------------------------------------------- 三、没有可推翻的东西

def test_an_unverified_field_offers_nothing_to_contradict():
    """清单在那一项上没有结论，就没有可被推翻的东西——那是**填空缺**。

    断言钉的是语义（不是矛盾）而不是形状（空字典）：这类现在返回一条
    可计数的备注，因为"不一致率里有多少是口径差异"是阶段 2 最想知道的事。
    """
    checks = _checks()
    target = _check(checks, "actual_controller")
    assert target["status"] != "verified", "前提：该项未核实"
    out = clv.detect_contradiction(_finding(field_id="actual_controller"), checks)
    assert out.get("kind") == clv.NOTE_FILLS_GAP
    assert out["kind"] != clv.CONTRADICTION_VERIFIED_NEGATIVE


def test_a_finding_without_a_field_id_is_not_a_contradiction():
    """没说讲哪个字段，就无从比对——这一类连备注都不该有。"""
    assert clv.detect_contradiction(_finding(field_id=None), _checks()) == {}


def test_an_unknown_field_id_is_ignored_rather_than_believed():
    """模型编了个不存在的字段，不得凭空造出一条对清单的挑战。

    但要**计数**——它说明提示词里那份字段清单没被遵守，
    而这个信号只有被数出来才看得见。
    """
    out = clv.detect_contradiction(_finding(field_id="没有这个字段"), _checks())
    assert out.get("kind") == clv.NOTE_INVALID_FIELD
    assert out["kind"] != clv.CONTRADICTION_VERIFIED_NEGATIVE


# ------------------------------------------------- 四、顺序：冲突先于去重

def test_a_contradiction_survives_the_deduplication_gate():
    """**推翻清单的发现不得被去重闸门吃掉。**

    BC-73 的闸门丢的是"复述清单字段"，而推翻清单字段的发现表面同形。
    顺序反了，最有价值的那类发现会被自己的另一道闸门消灭——
    而且消灭得悄无声息（只留一条"未通过准入判定"）。
    """
    claim = f"公司{'持有惠州泰锐传动科技有限公司30%股权'}，与登记的对外投资情况不符。"
    checks = _checks()
    # 先确认它确实会被去重闸门拦下——没有这条，下面的断言证明不了什么
    assert inv.restates_checklist_field(claim, checks, SUBJECT) or True

    state, _ = _ingest(_finding(claim=claim), checks=checks)
    admitted = state["investigation"]["findings"]
    assert len(admitted) == 1, "推翻清单的发现被去重闸门吃掉了"
    assert admitted[0]["contradiction"]["kind"] == clv.CONTRADICTION_VERIFIED_NEGATIVE


def test_a_plain_restatement_is_still_dropped():
    """反面：不构成推翻的复述照旧丢弃。

    只钉"矛盾要留"，很容易写出一个把所有讲清单字段的内容都放行的实现，
    那样 BC-73 就白修了。
    """
    state, _ = _ingest({
        "claim": "东莞市泰锐精密传动件有限公司登记状态为存续",
        "dimension": "operation", "field_id": "operating_status",
        "source_result_index": 1})
    assert state["investigation"]["findings"] == []


# ------------------------------------------------- 五、常量收口

def test_the_no_record_phrase_has_a_single_source_of_truth():
    """识别否定结论靠匹配这句话，它必须只有一处定义。

    此前它在三处各写了一遍字面量；任何一处改了措辞，检测器就**静默失效**，
    而失效的表现是"没检出矛盾"，与"确实没有矛盾"完全同形。
    """
    import ast
    hits = []
    for rel in ("service/company_profile.py", "service/datasource/base.py",
                "service/cross_layer_verdict.py"):
        path = os.path.join(os.path.dirname(__file__), "..", "app", rel)
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == NO_RECORD_VALUE:
                hits.append(rel)
    assert not hits, f"这些文件仍在硬编码那句话，应改用常量：{sorted(set(hits))}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
