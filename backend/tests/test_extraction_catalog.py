# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
抽取目录必须等于本次运行实际启用的清单

## 这一轮在钉什么

BC-58 加的 14 个 factoring 场景字段接进了 `field_checks`、`completeness`、
评分器双覆盖率和报告缺口清单——**所有消费方都接上了，唯独没接产出方**。
`scout._analyze_search_results` 遍历的是 `CHECKLIST`（20 项核心的列表，
不含场景项），而提示词又明写"只能使用本章节上方列出的 field_id"。

于是实测 case_01：14/14 场景字段**零候选**，而语料里应收账款减值 32 条、
净额 31 条、海外收入 26 条正面命中。系统一直在精确报告这组字段
"覆盖率 0/14"——数字正确，含义完全误导：它们从未被尝试过。

这是 BC-49 形态的第六次（BC-31→BC-45→BC-48→BC-49→webfetch→本条）：
东西造好了，但没接到真正会执行的那条路上。

运行：cd backend && python -m pytest tests/test_extraction_catalog.py -q
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from config.dd_checklist import (  # noqa: E402
    ALL_SCENARIO_IDS, CHECKLIST, CHECKLIST_BY_ID, CORE_IDS,
)
from service.deep_research_v2.agents.scout import DeepScout  # noqa: E402


def _scout() -> DeepScout:
    return DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                     search_api_key="sk")


def _captured_prompt(section_id: str, active_field_ids, due_diligence=True) -> str:
    """跑一次真实的目录构造，把发给模型的 user_prompt 截下来。"""
    scout = _scout()
    captured = {}

    async def fake_llm(*args, **kwargs):
        captured["prompt"] = kwargs.get("user_prompt", "")
        return '{"field_evidence": []}'

    scout.call_llm = fake_llm
    asyncio.run(scout._analyze_search_results(
        "尽调", {"id": section_id, "title": "章节", "description": "d"},
        [{"title": "t", "summary": "s", "is_local": True}],
        due_diligence_mode=due_diligence,
        active_field_ids=active_field_ids,
    ))
    return captured.get("prompt", "")


def _catalog_ids(prompt: str) -> set[str]:
    block = prompt.split("## 固定核查字段")[-1].split("##")[0]
    return set(re.findall(r"^- ([a-z_0-9]+):", block, flags=re.MULTILINE))


ALL_ACTIVE = list(CORE_IDS) + list(ALL_SCENARIO_IDS)


def test_scenario_fields_reach_the_extraction_prompt():
    """本条的直接回归：sec_4 的 10 个场景字段必须出现在字段目录里。"""
    ids = _catalog_ids(_captured_prompt("sec_4", ALL_ACTIVE))
    expected = {f for f in ALL_SCENARIO_IDS if CHECKLIST_BY_ID[f].section_id == "sec_4"}
    assert expected, "前提：sec_4 确实挂了场景字段"
    missing = expected - ids
    assert not missing, f"场景字段没进抽取目录，模型不可能提出它们：{sorted(missing)}"


def test_catalog_equals_the_active_checklist_for_that_section():
    """目录必须**等于**该章节启用的字段集合，不多不少。

    少了 → 静默失明（本条）；多了 → 模型会为本次未启用的场景提候选，
    而那些 field_id 在 finalize 时会被当作"不属于清单"拒掉，白烧 token。
    """
    for section_id in ("sec_1", "sec_3", "sec_4", "sec_5", "sec_6"):
        ids = _catalog_ids(_captured_prompt(section_id, ALL_ACTIVE))
        expected = {f for f in ALL_ACTIVE
                    if CHECKLIST_BY_ID[f].section_id == section_id}
        assert ids == expected, f"{section_id} 目录与启用清单不一致：{ids ^ expected}"


def test_disabled_scenario_is_not_offered():
    """没启用场景的运行，目录里不得出现场景字段。

    互为反面：修复不能变成"把所有字段都塞给模型"。
    """
    ids = _catalog_ids(_captured_prompt("sec_4", list(CORE_IDS)))
    leaked = ids & set(ALL_SCENARIO_IDS)
    assert not leaked, f"未启用的场景字段泄漏进目录：{sorted(leaked)}"
    assert "revenue" in ids, "核心字段仍必须在"


def test_candidate_budget_scales_with_the_field_count():
    """预算必须跟着字段数走，否则接上的目录会被一个没改的常数抵消。

    原来写死 12：那是 sec_4 只有 4 个核心财务字段时标定的（4 × 近三期）。
    接入 14 项后 12 条连"每字段一条"都不够，模型被迫丢掉大部分场景字段。
    """
    prompt_wide = _captured_prompt("sec_4", ALL_ACTIVE)
    prompt_narrow = _captured_prompt("sec_7", ALL_ACTIVE)
    wide = int(re.search(r"总数最多 (\d+) 条", prompt_wide).group(1))
    narrow = int(re.search(r"总数最多 (\d+) 条", prompt_narrow).group(1))

    n_wide = len([f for f in ALL_ACTIVE if CHECKLIST_BY_ID[f].section_id == "sec_4"])
    assert wide >= n_wide, f"预算 {wide} 连每字段一条都不够（{n_wide} 个字段）"
    assert wide > narrow, "字段多的章节必须拿到更大的预算"
    assert wide <= 42, "预算仍要有上界，否则 BC-56 的输出爆炸会复发"


def test_falls_back_to_core_checklist_without_active_ids():
    """不传 active_field_ids 时（非尽调路径、单测直调）仍要能工作。"""
    ids = _catalog_ids(_captured_prompt("sec_4", None))
    assert ids == {i.field_id for i in CHECKLIST if i.section_id == "sec_4"}


def test_production_path_passes_the_active_checklist():
    """光有参数不够——生产入口必须真的把 field_checks 传进去。

    BC-49 的教训：纯函数测试无法回答"它有没有被接上"。这里直接断言
    调用点，防止参数加了却没人传（那正是本条的成因形态）。
    """
    import ast

    source = os.path.join(os.path.dirname(__file__), "..", "app", "service",
                          "deep_research_v2", "agents", "scout.py")
    tree = ast.parse(open(source, encoding="utf-8").read())
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_analyze_search_results"
    ]
    assert calls, "找不到生产调用点"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords}
        assert "active_field_ids" in kwargs,             f"生产调用点没有传 active_field_ids（实际：{sorted(kwargs)}）"
        passed = ast.unparse(next(kw.value for kw in call.keywords
                                  if kw.arg == "active_field_ids"))
        assert "field_checks" in passed,             f"必须从 state['field_checks'] 派生，而不是另建一份清单：{passed}"


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
