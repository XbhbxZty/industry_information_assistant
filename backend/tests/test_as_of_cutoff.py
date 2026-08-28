# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
研究截止日闸门（P0-3）

## 要解决的问题

系统此前有一整套时间机制——`retrieved_at`、`profile_retrieved_at()` 明令
禁止退化成 `now()`、证据替代的单调性检查、给 LLM 注入的时间基准——
但**没有一个作为输入的研究截止日**。所有时间逻辑的语义都是"取证时间必须
真实"，没有任何一处是"这条证据是否晚于我们要评估的那个时点"。

后果是回溯评测做不了：钉不住时点，就只能测"现在判断对不对"，
测不了"当时判断对不对"，而后者才是信贷模型真正被考核的维度。

## 最容易搞错的一件事

判据是证据的 `as_of_date`（事实何时发布/发生），**不是** `retrieved_at`
（我们何时取到）。今天跑一个截止日在去年的案子，所有证据的 retrieved_at
都是今天——拿它比截止日会把每一条都判成越界，机制退化成"永远不给评级"。

要卡的是另一件事：2025-07 才披露的半年报，不得进入 2025-05-31 时点的判断。

## 三档处理

| 情形 | 处理 | 理由 |
|---|---|---|
| 事实日期晚于截止日 | mismatch，阻断 | 用了当时不存在的信息，判断本身失效 |
| 未声明事实日期 | degradation + 专用闸门 | 不知道 ≠ 合规，但也不是确凿越界 |
| 事实日期早于截止日 | 通过 | — |

运行：cd backend && python tests/test_as_of_cutoff.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import build_field_checks, compute_completeness  # noqa: E402
from service.risk_scorecard import (  # noqa: E402
    GATE_AS_OF_UNKNOWN, GATE_PROVENANCE, apply_provenance_gate,
)
from service.verification import (  # noqa: E402
    REASON_AS_OF_DATE_UNKNOWN, REASON_INVALID_AS_OF_DATE, REASON_POST_CUTOFF_EVIDENCE,
    check_as_of, record_structured_evidence, register_adapter, unregister_adapter,
    verify_evidence_chain,
)

CUTOFF = "2025-05-31"
ADAPTER = "test_disclosure"


def _register():
    register_adapter(ADAPTER, "测试用披露文件适配器")


def _cleanup():
    unregister_adapter(ADAPTER)


def _check(field_id="litigation"):
    for c in build_field_checks(checked_at="2026-08-16T00:00:00"):
        if c["field_id"] == field_id:
            return c
    raise AssertionError(field_id)


def _write(store, chk, *, as_of_date, retrieved_at="2026-08-16T00:00:00"):
    return record_structured_evidence(
        store, chk,
        source_adapter=ADAPTER,
        status="verified",
        value="经查询，无相关记录",
        raw={"src": "test", "as_of_date": as_of_date},
        retrieved_at=retrieved_at,
        as_of_date=as_of_date,
    )


# ------------------------------------------------------ 一、check_as_of 本身

def test_no_cutoff_means_no_gate():
    """留空截止日必须与引入本机制之前行为完全一致。"""
    assert check_as_of({"as_of_date": "2099-01-01"}, "") is None


def test_fact_before_cutoff_passes():
    assert check_as_of({"as_of_date": "2025-03-15"}, CUTOFF) is None


def test_fact_on_cutoff_day_passes():
    """
    截止日当天的披露算在内。只给到日的截止日按当日 23:59:59 处理——
    否则 2025-05-31 发布的公告会被自己的截止日误杀。
    """
    assert check_as_of({"as_of_date": "2025-05-31"}, CUTOFF) is None
    assert check_as_of({"as_of_date": "2025-05-31T18:30:00"}, CUTOFF) is None


def test_fact_after_cutoff_is_blocked():
    problem = check_as_of({"as_of_date": "2025-07-31"}, CUTOFF)
    assert problem is not None
    assert problem[0] == REASON_POST_CUTOFF_EVIDENCE
    assert "2025-07-31" in problem[1] and CUTOFF in problem[1]


def test_missing_fact_date_is_degradation_not_pass():
    """
    "不知道这条事实是什么时候的"和"知道它在截止日之前"是两件事。
    放行等于默认后者，而这正是回溯评测要防的信息泄漏。
    """
    problem = check_as_of({"as_of_date": ""}, CUTOFF)
    assert problem is not None
    assert problem[0] == REASON_AS_OF_DATE_UNKNOWN


def test_retrieved_at_is_not_used_as_fact_date():
    """
    本文件最重要的一条：今天取到的、去年发布的证据必须通过。

    若实现拿 retrieved_at 比截止日，这条会失败，而且失败方式很隐蔽——
    整个机制会表现为"设了截止日就永远不给评级"。
    """
    ev = {"as_of_date": "2025-03-15", "retrieved_at": "2026-08-16T10:00:00"}
    assert check_as_of(ev, CUTOFF) is None, \
        "判据必须是事实发布日期，不能是我们何时取到它"


def test_invalid_cutoff_is_reported_not_ignored():
    problem = check_as_of({"as_of_date": "2025-01-01"}, "去年五月")
    assert problem is not None and problem[0] == REASON_INVALID_AS_OF_DATE


# --------------------------------------------- 二、写入口对 as_of_date 的校验

def test_illegal_as_of_date_rejected_at_write():
    """
    解析不出来的日期比没有更危险：它看起来像已经声明过了。
    """
    _register()
    try:
        store, chk = {}, _check()
        try:
            _write(store, chk, as_of_date="2025年3月")
        except ValueError as e:
            assert "as_of_date" in str(e)
            return
        raise AssertionError("非法 as_of_date 应当在写入口被拒")
    finally:
        _cleanup()


def test_as_of_date_is_persisted_on_evidence():
    _register()
    try:
        store, chk = {}, _check()
        ev_id = _write(store, chk, as_of_date="2025-03-15")
        assert store[ev_id]["as_of_date"] == "2025-03-15"
    finally:
        _cleanup()


# ------------------------------------------------- 三、重放校验中的三档处理

def _replay(store, chk, as_of):
    return verify_evidence_chain(
        {"name": "测试企业", "credit_code": "X"}, [chk], store, as_of=as_of,
    )


def test_post_cutoff_evidence_blocks_replay():
    _register()
    try:
        store, chk = {}, _check()
        _write(store, chk, as_of_date="2025-07-31")
        report = _replay(store, chk, CUTOFF)
        assert not report.ok, "越过截止日的证据必须阻断，不能只是降级"
        assert report.mismatches[0]["reason"] == REASON_POST_CUTOFF_EVIDENCE
    finally:
        _cleanup()


def test_pre_cutoff_evidence_passes_replay():
    _register()
    try:
        store, chk = {}, _check()
        _write(store, chk, as_of_date="2025-03-15")
        report = _replay(store, chk, CUTOFF)
        assert report.ok, f"截止日前的证据不该被拦：{report.mismatches}"
        assert not report.degradations
    finally:
        _cleanup()


def test_unknown_fact_date_degrades_but_does_not_block():
    _register()
    try:
        store, chk = {}, _check()
        _write(store, chk, as_of_date="")
        report = _replay(store, chk, CUTOFF)
        assert report.ok, "日期不明是信息不足，不是确凿越界，不该直接阻断"
        assert any(d["reason"] == REASON_AS_OF_DATE_UNKNOWN
                   for d in report.degradations), \
            "但必须留下降级记录，否则等于静默放行"
    finally:
        _cleanup()


def test_same_evidence_passes_when_no_cutoff_set():
    """向后兼容：不设截止日时，日期不明的证据不产生任何降级。"""
    _register()
    try:
        store, chk = {}, _check()
        _write(store, chk, as_of_date="")
        report = _replay(store, chk, "")
        assert report.ok and not report.degradations
    finally:
        _cleanup()


# ------------------------------------------------------------ 四、闸门理由

def _base_result():
    return {
        "level": "低风险",
        "gates_applied": [],
        "gate_kinds": [],
        "requires_human_review": False,
    }


def test_as_of_unknown_gets_its_own_gate_kind():
    """
    BC-18 的教训：闸门理由必须准确。事实日期不明是**时点**问题，
    混进"来源或取证时间不明"里报，风控人员会看到一条对不上的告警，
    然后学会无视它。
    """
    result = apply_provenance_gate(_base_result(), [
        {"field_id": "litigation", "reason": REASON_AS_OF_DATE_UNKNOWN,
         "research_as_of": CUTOFF, "detail": "未声明发布日期"},
    ])
    assert GATE_AS_OF_UNKNOWN in result["gate_kinds"]
    assert GATE_PROVENANCE not in result["gate_kinds"], \
        "时点问题不得伪装成来源问题"
    assert result["level"] != "低风险", "日期不明不得输出最宽松结论"
    assert result["requires_human_review"]
    assert any(CUTOFF in g for g in result["gates_applied"]), \
        "闸门措辞要写出是相对哪个截止日无法确认"


def test_provenance_and_as_of_gates_coexist_separately():
    result = apply_provenance_gate(_base_result(), [
        {"field_id": "litigation", "reason": REASON_AS_OF_DATE_UNKNOWN,
         "research_as_of": CUTOFF, "detail": "未声明发布日期"},
        {"field_id": "guarantee", "reason": "missing_origin",
         "detail": "旧检查点缺来源"},
    ])
    assert GATE_AS_OF_UNKNOWN in result["gate_kinds"]
    assert GATE_PROVENANCE in result["gate_kinds"]
    assert len(result["gates_applied"]) == 2, \
        "两类问题各报一条，合并成一条就丢掉了其中一个的处置路径"


def test_no_degradation_leaves_result_untouched():
    result = apply_provenance_gate(_base_result(), [])
    assert result["level"] == "低风险"
    assert not result["gate_kinds"]
    assert not result["requires_human_review"]


# ------------------------------------------------- 五、初始档案快照的时点

def test_profile_snapshot_after_cutoff_is_blocked():
    """
    档案是查询时点的快照：登记状态、涉诉记录都反映抓取当时的情形。
    因此对初始档案而言取证时间就是事实日期——一份 2026 年抓的档案
    不能用来支撑 2025-05-31 的判断。
    """
    chk = _check("registration")
    chk.update({
        "status": "verified", "value": "存续",
        "verification_origin": "initial_profile",
        "source_adapter": "initial_profile",
        "retrieved_at": "2026-08-09",
        "evidence_ids": [],
    })
    report = verify_evidence_chain(
        {"name": "测试企业", "credit_code": "X"}, [chk], {},
        profile_replay_fn=lambda c, f: {"registration": {"status": "verified",
                                                         "value": "存续"}},
        as_of=CUTOFF,
    )
    assert not report.ok
    assert report.mismatches[0]["reason"] == REASON_POST_CUTOFF_EVIDENCE


def test_profile_snapshot_before_cutoff_passes():
    chk = _check("registration")
    chk.update({
        "status": "verified", "value": "存续",
        "verification_origin": "initial_profile",
        "source_adapter": "initial_profile",
        "retrieved_at": "2025-04-01",
        "evidence_ids": [],
    })
    report = verify_evidence_chain(
        {"name": "测试企业", "credit_code": "X"}, [chk], {},
        profile_replay_fn=lambda c, f: {"registration": {"status": "verified",
                                                         "value": "存续"}},
        as_of=CUTOFF,
    )
    assert report.ok, f"截止日前的档案快照不该被拦：{report.mismatches}"


# --------------------------------------------- 六、Scout 网页结果的尽力过滤

def test_web_results_after_cutoff_are_dropped():
    from service.deep_research_v2.agents.scout import DeepScout
    s = DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                  search_api_key="k")
    kept = s._apply_as_of_filter([
        {"url": "a", "date": "2025-03-15"},
        {"url": "b", "date": "2025-07-31"},
    ], CUTOFF)
    urls = [r["url"] for r in kept]
    assert urls == ["a"], f"晚于截止日的网页应丢弃，实际保留 {urls}"


def test_web_results_without_date_are_kept_but_flagged():
    """
    丢弃日期不明的网页会让检索基本失效（大多数网页没有可靠发布日期）；
    静默保留则是假装做到了时点隔离。折中是保留 + 打标 + 由报告披露。
    """
    from service.deep_research_v2.agents.scout import DeepScout
    s = DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                  search_api_key="k")
    kept = s._apply_as_of_filter([{"url": "a", "date": ""}], CUTOFF)
    assert len(kept) == 1
    assert kept[0]["as_of_status"] == "unknown_date", \
        "日期不明必须打标，否则下游无从知道这条没经过时点过滤"


def test_web_filter_is_noop_without_cutoff():
    from service.deep_research_v2.agents.scout import DeepScout
    s = DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                  search_api_key="k")
    rows = [{"url": "a", "date": "2099-01-01"}]
    assert s._apply_as_of_filter(rows, "") == rows


# ------------------------------------------------------- 七、提示词时点约束

def test_time_anchor_carries_cutoff_when_set():
    from service.deep_research_v2.agents.scout import DeepScout
    s = DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                  search_api_key="k")
    assert "研究截止日" not in s._with_time_anchor("x"), \
        "未设截止日时不应凭空出现时点约束"
    s.as_of = CUTOFF
    anchored = s._with_time_anchor("x")
    assert "研究截止日" in anchored and CUTOFF in anchored
    assert "不得引用" in anchored, \
        "光给日期不够——BC-03 已证明模型的先验会压过上下文，必须给判定规则"


# ------------------------------------------------------- 八、附录里的披露
#
# 追踪失败与截止日，最终目的是让它们**出现在报告里**。只留在 state 里
# 而不进附录，等于 BC-48 的复现：机制建好了，一个字没进报告。

def _appendix(**kw):
    from service.evidence_appendix import render_appendix
    checks = build_field_checks(checked_at="2026-08-16T00:00:00")
    return render_appendix(checks, {}, compute_completeness(checks), **kw)


def test_appendix_states_the_cutoff_date():
    text = _appendix(as_of=CUTOFF)
    assert "研究截止日" in text and CUTOFF in text, \
        "一份没写明以哪天为准的尽调报告，事后无法判断当时该不该看到某条信息"


def test_appendix_lists_search_failures_separately():
    text = _appendix(search_failures=[{
        "provider": "judicial", "query": "测试企业 被执行",
        "failure_reason": "HTTP 403", "occurred_at": "2026-08-16T10:00:00",
    }])
    assert "未能完成的检索" in text
    assert "403" in text
    assert "不得据此认定不存在相关记录" in text, \
        "失败清单必须带免责声明，否则读者会把它读成一份『查过了』的记录"


def test_appendix_discloses_web_filter_limitation_when_cutoff_set():
    text = _appendix(as_of=CUTOFF)
    assert "检索时点局限" in text, \
        "网页检索做不到严格时点隔离，必须写明，不能假装做到了"


def test_appendix_omits_cutoff_sections_when_not_set():
    text = _appendix()
    assert "研究截止日" not in text and "检索时点局限" not in text, \
        "未设截止日时不该凭空出现时点章节"


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
