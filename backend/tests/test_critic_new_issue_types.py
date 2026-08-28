# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
Critic 新增两类尽调专项问题（P0-2）

## 来源

把案例包（`case_01` 宁德时代尽调评测案例）的 `self_check` 六条与系统现状
逐条对照后，确认两条完全没有对应机制：

| 案例包自检项 | 系统现状 |
|---|---|
| `major_conclusion_without_source` | ✅ `missing_source` |
| `inference_misrepresented_as_fact` | ✅ ≈ `unverified_as_fact` |
| `no_record_found_misrepresented_as_no_risk` | ✅ `absence_meaningful` + 纪律第 5 条 |
| `related_party_risk_misattributed_to_subject` | ❌ **无** → `subject_attribution_error` |
| `post_cutoff_information_used_in_cutoff_assessment` | ❌ **无** → `post_cutoff_evidence` |
| `inaccessible_link_used_as_positive_evidence` | 🟡 半个 → 见 test_search_outcome.py |

案例包给了主体归属的教科书样板：集团担保余额 692.98 亿，`conflict_note`
明写"不得自动归于上市主体单独债务"；`limitations` 再写一遍"集团或子公司的
担保、诉讼、债务不得未经主体识别自动归属"。

## 两条设计决定

1. **都进 `DD_BLOCKING_ISSUE_TYPES`**，不接受 severity 降级。
   主体归错不是措辞问题，是结论指向了另一个法人；越界取证不是"不够严谨"，
   是报告在描述一个当时不可能知道的世界。模型很容易把两者都标成 minor。

2. **`post_cutoff_evidence` 的提示词段落必须随截止日开关**。
   不设截止日还挂着它，等于让模型去找一个本次不存在的问题类型——
   与 BC-18 同形，而且更糟：那条永远亮的告警会主动制造误报。

运行：cd backend && python tests/test_critic_new_issue_types.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.deep_research_v2.agents.critic import CriticMaster  # noqa: E402
from service.review_verdict import (  # noqa: E402
    DD_BLOCKING_ISSUE_TYPES, VERDICT_MAJOR_ISSUES, derive_verdict,
    unresolved_blocking_issues,
)

NEW_TYPES = ("subject_attribution_error", "post_cutoff_evidence")


def _issue(t, sev="minor", **kw):
    return {"issue_type": t, "severity": sev, "description": "x", **kw}


def _critic():
    return CriticMaster(llm_api_key="k", llm_base_url="http://localhost:1/v1")


# ------------------------------------------------------------ 一、阻断语义

def test_两类新问题都在阻断清单里():
    assert set(NEW_TYPES) <= DD_BLOCKING_ISSUE_TYPES


def test_主体归属错误标minor也不得通过():
    """
    模型倾向于把主体归属当成"表述可以更精确"而标 minor。
    但集团担保写成借款人自身债务，不是措辞问题——
    结论指向的根本不是同一个法人。
    """
    r = derive_verdict([_issue("subject_attribution_error", "minor")],
                       9.0, llm_verdict="pass")
    assert r["verdict"] == VERDICT_MAJOR_ISSUES
    assert r["llm_verdict_overridden"]
    assert any("不接受严重度降级" in x for x in r["verdict_reasons"])


def test_越界取证标minor也不得通过():
    r = derive_verdict([_issue("post_cutoff_evidence", "minor")],
                       9.0, llm_verdict="pass")
    assert r["verdict"] == VERDICT_MAJOR_ISSUES
    assert r["llm_verdict_overridden"]


def test_两类新问题都进未解决阻断清单():
    """迭代用尽时它们必须能触发强制人工复核，而不是静默出厂。"""
    issues = [_issue(t, "minor") for t in NEW_TYPES]
    blocking = unresolved_blocking_issues(issues)
    assert {i["issue_type"] for i in blocking} == set(NEW_TYPES)


def test_未引入新类型时既有裁决不变():
    """回归护栏：新增类型不得改变原有判据。"""
    assert derive_verdict([_issue("missing_source", "minor")], 9.0)["verdict"] == "pass"


# -------------------------------------------------- 二、不得路由到补充检索

def test_两类新问题都不触发补充搜索():
    """
    主体归错是**写错了**，补充检索改不了它；
    越界信息要删掉或隔离，再搜只会搜到更多越界信息。
    把它们路由去补充检索是南辕北辙，且会造成 V1 式空转。
    """
    c = _critic()
    for t in NEW_TYPES:
        routing = c._analyze_issues_for_routing({
            "issues": [_issue(t, "critical", requires_new_search=True,
                              search_query="更多资料")],
            "missing_aspects": [],
        })
        assert not routing["should_research"], f"{t} 不该被路由到补充检索"


# ------------------------------------------ 三、截止日段落随开关出现/消失

def test_未设截止日时审核段落为空():
    """
    让模型去找一个本次不存在的问题类型，它会开始把正常的时间表述
    报成越界。永远挂着的检查项不是更安全，是更吵。
    """
    assert _critic()._format_as_of_section({}) == ""
    assert _critic()._format_as_of_section({"as_of": ""}) == ""
    assert _critic()._format_as_of_section({"as_of": "   "}) == ""


def test_设了截止日时段落带上具体日期():
    section = _critic()._format_as_of_section({"as_of": "2025-05-31"})
    assert "post_cutoff_evidence" in section
    assert "2025-05-31" in section, "光说『不得越界』没用，必须给出具体日期"
    assert "仅供后验参考" in section, \
        "要给出合规写法，否则模型只会删信息而不是隔离信息"


# ------------------------------------------------- 四、提示词模板可被渲染

def test_审核提示词模板在两种情形下都能渲染():
    """
    `{as_of_section}` 是新加的占位符。少传一个键会在生产路径上抛
    KeyError——而这条路径的异常会被吞成"审核不可用"，
    表现为整条审核链路静默降级。
    """
    c = _critic()
    for as_of in ("", "2025-05-31"):
        rendered = c.REVIEW_PROMPT.format(
            query="q", outline="o", draft_content="d",
            facts="f", data_points="dp", field_checks="fc",
            as_of_section=c._format_as_of_section({"as_of": as_of}),
        )
        assert "subject_attribution_error" in rendered, \
            "主体归属检查与截止日无关，两种情形下都必须在"
        # 判据是 **E 段是否出现**，不是字符串是否出现——
        # `post_cutoff_evidence` 永远在输出格式枚举里列着，那是应该的：
        # 枚举列全部可能取值，检查指令才随截止日开关。
        has_section = "使用了研究截止日之后的信息" in rendered
        assert has_section == bool(as_of), (
            f"截止日={as_of!r} 时 E 段应"
            f"{'出现' if as_of else '缺席'}，实际 {has_section}"
        )


def test_输出格式枚举包含两个新类型():
    """
    模型只会产出提示词里列过的 issue_type。枚举漏写 = 这两类永远检不出，
    而阻断规则会显得"从来没生效过"。
    """
    c = _critic()
    for t in NEW_TYPES:
        assert t in c.REVIEW_PROMPT, f"输出格式枚举缺少 {t}"


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
