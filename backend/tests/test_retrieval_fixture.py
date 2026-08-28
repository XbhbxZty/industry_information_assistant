# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
固定检索输入，让模型消融可归因（BC-56 仍待完成的那一条）

## 这一轮在钉什么

BC-56 需要的对照是"同样的证据、不同的抽取模型"。直接换 Scout 模型跑两遍**给不到**
这个对照：Architect 也是 LLM，它的章节标题、描述和检索词会漂，检索词变了检索结果
就变，检索结果变了 Scout 能抽到什么也就变了。这样测出来的覆盖率差异无法归因给
抽取能力——BADCASES 记为"规划查询变化污染归因"。

所以录制一次上游输入，之后逐字回放。剩下的唯一自由变量才是 Scout 模型。

## 断言分三层

1. **录制/回放的保真**：提纲、检索结果、以及**检索失败**都要照原样重放。
   失败被回放成"查了没有"，报告里那句"未发现相关记录"就成了无依据的正面结论（BC-51）。
2. **补丁不得泄漏**：装置打在 eval 层，用完必须撤干净——留着会静默影响同进程内
   后续任何一次运行。生产代码里不能有 replay 分支（BC-45：替身活在生产路径上）。
3. **消融不得冒充生产成绩**：回放运行的 verdict 必须单独一档，
   不能被当成"系统达标了"（BC-25：生产降级污染实验归因）。

运行：cd backend && python -m pytest tests/test_retrieval_fixture.py -q
"""
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND))
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(BACKEND / "eval"))

import retrieval_fixture  # noqa: E402
from retrieval_fixture import RetrievalFixture, install  # noqa: E402
from service.deep_research_v2.agents.architect import ChiefArchitect  # noqa: E402
from service.deep_research_v2.agents.scout import DeepScout, SearchOutcome  # noqa: E402
from service.deep_research_v2.state import ResearchPhase  # noqa: E402
from eval import score_real_case_run  # noqa: E402


def _result_row(index: int, source_id: str = "S001") -> dict:
    return {
        "is_local": True,
        "kb_id": "case_01",
        "doc_id": f"doc-{index}",
        "chunk_index": index,
        "url": f"local://kb/case_01/doc-{index}",
        "summary": f"[case_id=case_01; source_id={source_id}; locator=page:{index}] 文本 {index}",
        "title": f"{source_id}_{source_id}.pdf",
    }


def _recorded_fixture() -> RetrievalFixture:
    fixture = RetrievalFixture({"case_id": "case_01"})
    fixture.note_plan({
        "outline": [{"id": "sec_1", "title": "企业基本情况", "description": "d1",
                     "search_queries": ["统一社会信用代码"]},
                    {"id": "sec_4", "title": "财务分析", "description": "d4",
                     "search_queries": ["营业收入"]}],
        "hypotheses": [{"id": "h_1", "content": "假设", "status": "unverified"}],
        "research_questions": ["q1"],
    })
    fixture.note_search("sec_1", "统一社会信用代码", [_result_row(1)], True, "")
    fixture.note_search("sec_4", "营业收入", [_result_row(2), _result_row(3)], True, "")
    fixture.note_search("sec_4", "资产负债率", [_result_row(3)], True, "")   # 重复项去重
    fixture.note_search("sec_5", "涉诉记录", [], False, "Milvus 服务不可用")
    return fixture


# ------------------------------------------------------- 一、录制与回放的保真

def test_recorded_results_are_deduplicated_per_section():
    fixture = _recorded_fixture()
    assert [row["chunk_index"] for row in fixture.replay_results("sec_4")] == [2, 3], \
        "同一片段被多个检索词命中时只应留一份"
    assert len(fixture.replay_results("sec_1")) == 1


def test_failed_search_is_recorded_as_a_failure_not_as_an_empty_result():
    """录制时失败的检索，回放必须仍然是失败。

    把它回放成"查了没有"，附录就会少一条免责声明，而报告里那句
    "未发现相关记录"变成了无依据的正面结论——BC-51 的原样复发。
    """
    fixture = _recorded_fixture()
    assert fixture.replay_results("sec_5") == []
    failures = fixture.failed_searches("sec_5")
    assert len(failures) == 1 and "Milvus" in failures[0]["failure_reason"]


def test_round_trip_through_disk_is_byte_stable(tmp_path):
    fixture = _recorded_fixture()
    path = tmp_path / "fixture.json"
    digest = fixture.save(path)
    reloaded = RetrievalFixture.load(path)
    assert reloaded.content_hash() == digest, "同一份装置的摘要必须稳定，否则无法证明两轮吃的是同一份输入"
    assert reloaded.replay_outline() == fixture.replay_outline()
    assert reloaded.replay_results("sec_4") == fixture.replay_results("sec_4")


def test_incompatible_or_empty_fixture_is_rejected(tmp_path):
    bad_version = tmp_path / "v99.json"
    bad_version.write_text(json.dumps({"version": 99, "outline": [{"id": "sec_1"}]}),
                           encoding="utf-8")
    try:
        RetrievalFixture.load(bad_version)
    except ValueError as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("版本不匹配的装置必须拒绝回放，而不是静默降级")

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"version": retrieval_fixture.FIXTURE_VERSION}),
                     encoding="utf-8")
    try:
        RetrievalFixture.load(empty)
    except ValueError as exc:
        assert "outline" in str(exc)
    else:
        raise AssertionError("没有提纲的装置钉不住抽取输入，必须拒绝")


# --------------------------------------------------- 二、补丁不得泄漏到生产

def test_install_restores_every_patched_method():
    original = (ChiefArchitect.process, DeepScout._execute_local_search,
                DeepScout._research_section)
    undo = install(_recorded_fixture(), "replay")
    assert (ChiefArchitect.process, DeepScout._execute_local_search,
            DeepScout._research_section) != original, "补丁应当真的装上了"
    undo()
    assert (ChiefArchitect.process, DeepScout._execute_local_search,
            DeepScout._research_section) == original, \
        "补丁必须撤干净——留着会静默影响同进程内后续任何一次运行"


def test_production_code_contains_no_replay_branch():
    """生产代码里不得出现回放**分支**（BC-45）。

    替身活在生产路径上，就会出现"替身比真货强、真货的缺陷被掩盖两个版本"
    那种事。装置只能由 eval 层从外部注入。

    ⚠️ 判据是**代码**，不是"提到过这个名字"。第一版用裸子串匹配，结果被
    `llm_config.py` 文档字符串里一句"消融装置（eval/retrieval_fixture.py）已就绪"
    命中——一个假阳性，而它会推着人去删掉一句正确且有用的注释。
    这正是 BC-59 的形状：判据没校准，就会把正确的东西判成错的。
    在生产代码里**指路**说明装置在哪，是好事，不是违规。
    """
    app_dir = BACKEND / "app"
    # 真正构成"生产依赖回放"的形态：导入该模块、调用它的接口、或按模式分支。
    forbidden = (
        "import retrieval_fixture",
        "from retrieval_fixture",
        "from eval.retrieval_fixture",
        "replay_results(",
        "replay_outline(",
        'fixture_mode ==',
        'fixture_mode !=',
        'fixture_mode)',
    )
    offenders = []
    for path in app_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(app_dir)}:{token}")
    assert not offenders, f"生产代码出现了对评测装置的真实依赖：{offenders}"


def test_the_no_replay_branch_guard_actually_catches_a_violation(tmp_path):
    """守卫必须真的会红——否则它只是一条永远通过的装饰。

    BC-61 的教训：一条因为错误原因通过的断言，等于没有断言。
    这里造一个假的 app 目录，确认同一套判据能抓到真实违规形态。
    """
    fake_app = tmp_path / "app" / "service"
    fake_app.mkdir(parents=True)
    (fake_app / "clean.py").write_text(
        "# 消融装置见 eval/retrieval_fixture.py，本文件不依赖它\n", encoding="utf-8")
    (fake_app / "dirty.py").write_text(
        "from retrieval_fixture import RetrievalFixture\n", encoding="utf-8")

    forbidden = ("import retrieval_fixture", "from retrieval_fixture", "replay_results(")
    hits = []
    for path in (tmp_path / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append(path.name)
    assert hits == ["dirty.py"], \
        f"判据要抓到真实导入、且不误伤纯指路注释，实际命中：{hits}"


def test_mode_must_be_record_or_replay():
    try:
        install(_recorded_fixture(), "sometimes")
    except ValueError as exc:
        assert "record" in str(exc)
    else:
        raise AssertionError("非法模式必须当场拒绝")


# ---------------------------------------------- 三、回放真的冻结了抽取输入

def test_replay_pins_the_outline_and_skips_the_planning_call():
    """回放时 Architect 不发 LLM 调用，提纲逐字来自装置。"""
    fixture = _recorded_fixture()
    undo = install(fixture, "replay")
    try:
        architect = ChiefArchitect("k", "http://localhost:1/v1", "m")
        called = {"llm": False}

        async def forbidden(*args, **kwargs):
            called["llm"] = True
            raise AssertionError("回放时不应发生规划模型调用")

        architect.call_llm = forbidden
        state = {"phase": ResearchPhase.INIT.value, "query": "q", "messages": [],
                 "session_id": "s", "key_entities": []}
        state = asyncio.run(architect.process(state))
    finally:
        undo()

    assert not called["llm"]
    assert [section["id"] for section in state["outline"]] == ["sec_1", "sec_4"]
    assert state["outline"][1]["title"] == "财务分析"
    assert state["hypotheses"][0]["id"] == "h_1"
    assert state["phase"] == ResearchPhase.PLANNING.value
    assert any(m.get("type") == "outline" for m in state["messages"]), \
        "提纲事件照发：事件流是评测与前端的共同契约"


def _drive_section(fixture, section_id, query):
    """在 section 上下文里发一次检索，返回 SearchOutcome。

    把 `_research_section` 的内层换成一个只发检索的存根，**再**装装置——
    这样外层的 section 记录包装是真实的那个，被测的正是它。
    """
    real_section = DeepScout._research_section
    captured = {}

    async def stub(self, state, section):
        captured["outcome"] = await self._execute_local_search(query)

    DeepScout._research_section = stub
    undo = install(fixture, "replay")
    try:
        scout = DeepScout("k", "http://localhost:1/v1", "sk")
        scout.milvus_service = None          # 回放不得触达 Milvus
        asyncio.run(DeepScout._research_section(scout, {"kb_scope": []},
                                               {"id": section_id}))
    finally:
        undo()
        DeepScout._research_section = real_section
    return captured["outcome"]


def test_replay_serves_recorded_results_regardless_of_query_text():
    """按 section 键回放，而不是按检索词。

    这是整个装置的关键设计：换一个模型就会写出不同的检索词，按检索词键会
    **一条都命中不到**，装置等于没装——而失效是静默的（返回空集，看起来像
    "这一章没材料"）。
    """
    fixture = _recorded_fixture()
    outcome = _drive_section(fixture, "sec_4", "一个录制时根本没出现过的检索词")
    assert outcome.ok
    assert [row["chunk_index"] for row in outcome.results] == [2, 3], \
        "检索词变了，回放的证据必须一模一样"


def test_replay_reproduces_a_recorded_failure_as_a_failure():
    fixture = _recorded_fixture()
    outcome = _drive_section(fixture, "sec_5", "涉诉记录")
    assert not outcome.ok, "录制时失败的章节，回放必须仍是失败而不是空结果"
    assert "Milvus" in outcome.failure_reason
    assert "[fixture]" in outcome.failure_reason, "回放来源要能被认出来"


def test_replay_of_an_unrecorded_section_is_empty_but_successful():
    """录制里没有这个 section，且没有失败记录 → 空结果、成功。

    这是"查了没有"，不是故障：装置里确实没有它的证据，
    但也没有任何失败发生过，不能凭空编一个故障出来。
    """
    fixture = _recorded_fixture()
    outcome = _drive_section(fixture, "sec_7", "负面舆情")
    assert outcome.ok and outcome.results == []


def test_recording_captures_what_production_actually_retrieved():
    """录制模式必须透传真实结果，且如实记下失败。"""
    fixture = RetrievalFixture({"case_id": "case_01"})
    real_section = DeepScout._research_section
    real_local = DeepScout._execute_local_search

    async def fake_local(self, query, top_k=10, kb_scope=None):
        if "涉诉" in query:
            return SearchOutcome(ok=False, failure_reason="超时",
                                 provider="local_kb", query=query)
        return SearchOutcome(results=[_result_row(9)], ok=True,
                             provider="local_kb", query=query)

    async def stub(self, state, section):
        await self._execute_local_search(section["probe"])

    DeepScout._execute_local_search = fake_local
    DeepScout._research_section = stub
    undo = install(fixture, "record")
    try:
        scout = DeepScout("k", "http://localhost:1/v1", "sk")
        asyncio.run(DeepScout._research_section(scout, {}, {"id": "sec_4", "probe": "营业收入"}))
        asyncio.run(DeepScout._research_section(scout, {}, {"id": "sec_5", "probe": "涉诉记录"}))
    finally:
        undo()
        DeepScout._research_section = real_section
        DeepScout._execute_local_search = real_local

    assert [row["chunk_index"] for row in fixture.replay_results("sec_4")] == [9]
    assert fixture.replay_results("sec_5") == []
    assert fixture.failed_searches("sec_5")[0]["failure_reason"] == "超时"


# ------------------------------- 四、消融不得冒充生产成绩（BC-25 的形态）

def _synthetic_run(tmp_path: Path, fixture_mode: str) -> Path:
    run_dir = tmp_path / f"run-{fixture_mode}"
    run_dir.mkdir()
    final_event = {
        "type": "research_complete",
        "final_report": "报告正文" * 300,
        "risk_assessment": {"requires_human_review": True},
        "errors": [],
        "section_failures": [],
    }
    (run_dir / "result.json").write_text(json.dumps({
        "case_id": "case_01", "session_id": f"unit-{fixture_mode}", "status": "completed",
        "research_cutoff": "2025-05-31", "search_web": False, "search_local": True,
        "kb_scope": [{"kb_id": "case_01"}], "reference_layer_read": False,
        "post_cutoff_layer_read": False,
        "agent_models": {"scout": "deepseek-v4-flash"},
        "retrieval_fixture": {"mode": fixture_mode, "content_sha256": "abc123"},
        "final_event": final_event,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "report.md").write_text(final_event["final_report"], encoding="utf-8")
    (run_dir / "events.jsonl").write_text(json.dumps({
        "type": "search_results", "as_of": "2025-05-31",
        "content": {"searchType": "local", "results": [{
            "url": "local://kb/case_01/doc-a",
            "snippet": "[case_id=case_01; source_id=S001; locator=page:1] 文本",
        }]},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return run_dir


def test_replayed_run_is_scored_as_an_ablation_not_as_pass_or_fail(tmp_path):
    score = score_real_case_run.score_run(_synthetic_run(tmp_path, "replay"))
    assert score["verdict"] == "ablation", \
        "回放运行冻结了规划与检索，它的分数不能当成系统达标与否的结论"
    assert score["full_pipeline_run"] is False
    assert score["retrieval_fixture"]["content_sha256"] == "abc123", \
        "分数必须能指回它吃的是哪一份冻结输入"
    assert score["agent_models"]["scout"] == "deepseek-v4-flash", \
        "分数必须能指回是哪个节点用了哪个模型"


def test_full_run_keeps_the_normal_pass_fail_track(tmp_path):
    score = score_real_case_run.score_run(_synthetic_run(tmp_path, "none"))
    assert score["verdict"] in {"pass", "fail"}
    assert score["full_pipeline_run"] is True


if __name__ == "__main__":
    import tempfile

    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            if fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(Path(tmp))
            else:
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
