# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
探索性抽取的失败必须与「材料里没东西」可分辨（BC-74）

## 这一轮在钉什么

阶段 2 冒烟两轮，两个主体都交了 0 条发现，留痕里**一条失败都没有**。
我差点据此断定"语料里没有清单以外的内容"，转头去造语料。

真相是：真实语料让模型写出 8884 字的 JSON，`max_tokens=4000` 从中间切断，
`parse_json_response` 解析失败——而它失败时返回的是 **`{}`，不是 None**。

于是这道守卫**永远不会触发**：

    payload = self.parse_json_response(response)
    if not isinstance(payload, dict):     # {} 是 dict
        record_failure(...)

`{}` 一路走到 `ingest_exploratory_payload`，产出 0 发现、0 拒绝、0 留痕，
**外观与"查了没有"完全一致**。

这是两条既有纪律在我自己代码里的复发：
BC-51「查了没有 vs 没查成」，以及 BC-72「守卫存在 ≠ 守卫正确」。

## 所以这里钉的是三件事

1. 解析失败要被记成 `error`，不能变成"没发现"
2. 空响应同样要留痕
3. **正常的空数组不得被误报成失败**——否则就从一个极端跑到另一个极端

运行：cd backend && python -m pytest tests/test_exploratory_pass_failures.py -q
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service import investigation_layer as inv  # noqa: E402
from service.deep_research_v2.agents.wizard import CodeWizard  # noqa: E402


def _wizard(response, finish_reason=""):
    """构造一个把 `call_llm` 换成固定响应的 CodeWizard。

    ⚠️ 替身**必须遵守 `return_meta` 契约**。此前它一律返回裸字符串，
    而生产已经改成解包二元组——测试当场炸成
    `too many values to unpack`，那还算走运：
    真正危险的是替身与生产悄悄分叉、测试却仍然全绿（本 session 已撞两次）。
    """
    wizard = CodeWizard(llm_api_key="x", llm_base_url="http://127.0.0.1:1/v1")

    async def _fake(*_args, return_meta=False, **_kwargs):
        if return_meta:
            return response, {"finish_reason": finish_reason,
                              "completion_tokens": None, "duration_ms": 1}
        return response

    wizard.call_llm = _fake
    return wizard


def test_the_double_matches_the_real_call_llm_signature():
    """替身与真实签名必须对得上。

    夹具与生产分叉时，测的就是一个已不存在的系统——
    这条把"签名有没有 return_meta"钉住，让分叉在第一时间失败。
    """
    import inspect
    from service.deep_research_v2.agents.base import BaseAgent
    assert "return_meta" in inspect.signature(BaseAgent.call_llm).parameters


def _state():
    return {
        "company_name": "云岭恒晟精密机械有限公司",
        "as_of": "2026-08-22",
        "field_checks": [],
        "messages": [],
        "raw_sources": [{
            "title": "云岭恒晟精密机械有限公司 2025 年度报告",
            "url": "kb://a.pdf", "site_name": "本地知识库",
            "date": "2026-03-20", "retrieved_at": "2026-08-22T10:00:00",
            "summary": "云岭恒晟精密机械有限公司主要客户为三家整车厂……",
        }],
    }


def _run(response, finish_reason=""):
    state = _state()
    asyncio.run(_wizard(response, finish_reason)._run_exploratory_pass(state))
    return state


def _failures(state, stage="探索性调查"):
    return [f for f in (state.get("investigation") or {}).get("failures") or []
            if f["stage"] == stage]


# ------------------------------------------------- 一、解析失败要被抓到

def test_truncated_json_is_recorded_as_an_error_not_as_nothing_found():
    """本条的直接回归：截断的 JSON 必须留下 `error` 留痕。

    真实现象就是这样——响应以 `{"findings": [` 开头、在中间被切断。
    """
    truncated = '{\n  "findings": [\n    {\n      "claim": "公司主要客户为三家整车厂'
    state = _run(truncated)
    failures = _failures(state)
    assert failures, "解析失败没有留下任何痕迹——与「查了没有」不可分辨"
    assert failures[0]["kind"] == "error"


def test_a_short_incomplete_response_is_not_blamed_on_max_tokens():
    """**归因要站得住**（BC-76）。

    阶段 2 观察期实测两轮：响应 1948 字与 3546 字，而输出预算是
    8000 token（约 12000 字）——离上界差一个数量级。
    上一版一律写"疑似被 max_tokens 截断"，说了一件不成立的事，
    而下一个人会顺着那个成因去查。
    """
    truncated = '{"findings": [{"claim": "某条被切断的陈述内容在这里'
    reason = _failures(_run(truncated))[0]["reason"]
    assert "远未达输出上界" in reason
    assert "成因不是 max_tokens" in reason


def test_a_response_near_the_budget_is_blamed_on_max_tokens():
    """反面：真的接近上界时，就要明说是上界截断。

    只钉"别乱说截断"，很容易写出一个永远不提截断的实现——
    那样 BC-74 那种真截断就失去了最直接的线索。
    """
    from service.deep_research_v2.agents.wizard import CodeWizard as _CW
    budget_chars = _CW.INVESTIGATION_MAX_TOKENS * _CW.OUTPUT_CHARS_PER_TOKEN
    huge = '{"findings": [{"claim": "' + "内容" * int(budget_chars * 0.45)
    reason = _failures(_run(huge))[0]["reason"]
    assert "输出上界截断" in reason


def test_complete_but_unparseable_response_is_not_blamed_on_truncation():
    """反面：闭合了的响应不得被说成截断——错误的归因会误导排查。"""
    reason = _failures(_run("这不是 JSON，但它是完整的一句话。"))[0]["reason"]
    assert "非截断" in reason
    assert "远未达输出上界" not in reason


def test_the_detail_carries_the_tail_not_only_the_head():
    """留痕要带**结尾**——截断的证据在那里。

    上一版只存开头 120 字，于是那条"疑似被截断"的归因
    **既不成立、又无法被留痕证伪**。
    """
    truncated = '{"findings": [{"claim": "' + "甲" * 300 + "乙尾巴标记"
    detail = _failures(_run(truncated))[0]["detail"]
    assert "乙尾巴标记" in detail, "留痕没带结尾，无法判断是否真的被切断"
    assert "括号" in detail, "留痕要带括号配平计数，那是归因的直接依据"


def test_length_finish_reason_is_believed_over_the_length_heuristic():
    """供应商说 `finish_reason=length` 时，就按上界截断记。

    它比任何长度启发式都可靠——启发式只能看见"括号不平"，
    看不见"为什么不平"（BC-76 的整条教训）。
    """
    short_but_cut = '{"findings": [{"claim": "很短但确实被上界切断了'
    reason = _failures(_run(short_but_cut, finish_reason="length"))[0]["reason"]
    assert "finish_reason=length" in reason


def test_normal_stop_with_broken_json_is_an_instruction_following_problem():
    """模型自报正常结束却交出残缺 JSON——那是指令遵循问题，不是预算问题。

    **不得归因到 max_tokens**：顺着预算去查会白费功夫，
    而真正该做的是改提示词或换模型。
    """
    reason = _failures(_run('{"findings": [{"claim": "残缺',
                            finish_reason="stop"))[0]["reason"]
    assert "指令遵循" in reason
    assert "max_tokens" not in reason.replace("与输出上界无关", "")


def test_the_detail_records_the_finish_reason():
    """留痕要带 finish_reason——归因的直接依据。"""
    detail = _failures(_run('{"findings": [{"a', finish_reason="stop"))[0]["detail"]
    assert "finish=stop" in detail


def test_empty_response_is_recorded_too():
    state = _run("")
    failures = _failures(state)
    assert failures and failures[0]["kind"] == "error"
    assert "空响应" in failures[0]["reason"]


# ------------------------------------------------- 二、不得从一个极端到另一个极端

def test_a_legitimate_empty_payload_is_recorded_as_not_found_not_as_error():
    """**材料里确实没有可写的东西是常态**，不得记成故障。

    但它也不能一声不吭：跑完一无所获与根本没跑，在报告上都是"这一节空着"，
    不写明读者无从判断该不该补语料（BC-51）。

    所以钉的是 `kind`——三态设计（not_found / error / disabled）
    的全部意义就在这个区分上。
    """
    state = _run(json.dumps({"findings": [], "relations": [], "metrics": []}))
    failures = _failures(state)
    assert len(failures) == 1
    assert failures[0]["kind"] == "not_found", "正常空结果不得记成故障"
    assert "没有清单以外" in failures[0]["reason"]


def test_the_not_found_note_does_not_appear_when_findings_exist():
    """反面：有产出时不得留这条。

    恒定出现的留痕不携带信息，读者很快学会跳过整段（BC-18 的形态）。
    """
    payload = {"findings": [{
        "claim": "公司主要客户为三家整车厂，2025 年合计占营业收入约六成",
        "dimension": "operation", "source_result_index": 1,
    }]}
    state = _run(json.dumps(payload, ensure_ascii=False))
    assert _failures(state) == []


def test_a_normal_payload_still_produces_findings():
    """反面兜底：正常响应必须照常走通准入。"""
    payload = {"findings": [{
        "claim": "公司主要客户为三家整车厂，2025 年合计占营业收入约六成",
        "dimension": "operation", "direction": "aggravating",
        "materiality": "medium", "source_result_index": 1,
    }]}
    state = _run(json.dumps(payload, ensure_ascii=False))
    assert len(state["investigation"]["findings"]) == 1
    assert _failures(state) == []


# ------------------------------------------------- 三、上界与提示词要对得上

def test_the_prompt_tells_the_model_the_same_cap_the_code_enforces():
    """条数上限必须在提示词与代码里是同一个数。

    两处各写一个数就是「一条规则有两份实现」（BC-52/BC-59 的形态）——
    而且这里的分叉会以"输出被截断、整份作废"的形式出现，
    比数字对不上更难查。
    """
    rendered = inv.EXPLORATORY_PROMPT.format(
        subject="X", as_of="2026-01-01", sources="S",
        max_findings=inv.MAX_FINDINGS)
    assert f"最多写 {inv.MAX_FINDINGS} 条" in rendered


def test_output_budget_is_large_enough_for_the_capped_findings():
    """输出上界要装得下上限条数的发现。

    实测：20 条来源能让模型写出 8884 字 JSON，4000 token 会切断它。
    这条断言不是精确建模，是一道防止有人把上界改小回去的护栏。

    读常量而不是读调用点的字面量：BC-76 之后预算被收成
    `INVESTIGATION_MAX_TOKENS`，因为诊断函数要拿它判断"截断是否可能"——
    两处各写一个数，归因就会骗人。
    """
    assert CodeWizard.INVESTIGATION_MAX_TOKENS >= 8000, "上界太小，会重演 BC-74"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
