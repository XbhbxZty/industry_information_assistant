# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""阶段 2 观察期 runner —— 连续多轮，采集跨层一致性判定

## 为什么必须另起一个入口

`run_real_case_agent.py` 按计划 9.1 **强制关闭调查层**：封闭评测要可复现，
同一份语料两次跑出不同的外部结果，分数就失去可比性。

但阶段 2 要观察的恰恰是 B 层的发现如何挑战 A 层结论——**关掉它，
判定恒为「一致」，跑多少轮都是零信息**。两个入口的目的不同，
不该为了少写一个文件把那道护栏放宽。

所以本脚本：

- `investigation=True`（与封闭评测相反，这是本期的全部意义）
- `search_web=False`（A 层仍要可复现——变的只该是 B 层）
- 输出到 `eval/runs/stage2/`，与封闭评测的产物物理分开

## 为什么要重复跑同一个主体

计划 9.3：已实测存在**轮间方差**——同配置同输入，`shareholders` /
`cash_flow` 在不同轮次之间会翻转。不做重复就无法区分
「两层真分歧」与「模型随机性」，而这正是阶段 4 要拿来做决策的那个数。

用法：
    python eval/run_stage2_observation.py --repeats 5            # 全部主体
    python eval/run_stage2_observation.py --subjects mock002 --repeats 3
    python eval/run_stage2_observation.py --list                 # 只看主体表
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

OUTPUT_ROOT = BACKEND / "eval" / "runs" / "stage2"


def _code_rev() -> str:
    """当前 git HEAD 短 sha，刻进每一轮的 result.json。

    第一批观察是带着 BC-75（语料被压成 1 片）跑出来的，而这件事只记在
    我的记忆里——产物本身完全看不出来。**一批实验数据必须自带它的代码版本**，
    否则几个月后没人能回答「这批数是在哪些修复之前还是之后」。
    """
    sha = _git("rev-parse", "--short", "HEAD") or "unknown"
    # ⚠️ 工作区脏的时候，sha **描述不了跑的是什么代码**。
    #    本项目当前正是这种状态：HEAD 在阶段 1 之前，而调查层、
    #    BC-71～77 的修复全在工作区里。只刻 sha 会把这批数据标成
    #    「不含调查层的版本跑出来的」——比不刻还糟。
    return f"{sha}-dirty" if _git("status", "--porcelain") else sha


def _git(*args: str) -> str:
    import subprocess  # noqa: WPS433
    try:
        out = subprocess.run(["git", *args], cwd=BACKEND, capture_output=True,
                             text=True, timeout=20, check=False)
        return (out.stdout or "").strip()
    except Exception:                                     # noqa: BLE001
        return ""


def _write_batch_meta(out_root: "Path", keys: List[str], repeats: int) -> None:
    """把「这批是什么条件下跑的」落进批次目录本身。

    第一批观察缺的就是这个：它带着 BC-75 跑完 15 轮，而产物里没有
    任何一处记着这件事。批次目录名会被改、记忆会过期，**只有落盘的
    元数据跟着数据走**。
    """
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "batch_meta.json").write_text(json.dumps({
        "batch": out_root.name,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "code_rev": _code_rev(),
        "head_subject": _git("log", "-1", "--pretty=%s"),
        "dirty_files": [l for l in _git("status", "--porcelain").split("\n") if l],
        "subjects": {k: SUBJECTS[k]["label"] for k in keys},
        "repeats": repeats,
        "investigation": True,
        "search_web": False,
        "known_sample_gaps": MISSING_EXITS,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

#: 观察主体表。
#:
#: 9.3 要 6 个主体 × 5 次重复，覆盖三类**不同的 A 层出口**——
#: 否则观察到的不一致会偏向单一形态。目前只有两个主体同时满足
#: 「有档案或语料」且「B 层有东西可查」，缺口如实记在这里而不是假装齐了。
SUBJECTS: Dict[str, Dict[str, Any]] = {
    "mock002": {
        "label": "云岭恒晟（可授信）",
        "exit": "可授信",
        "kind": "mock",
        "subject": "云岭恒晟精密机械有限公司",
        "business_type": "factoring",
        "collection": "mock_yunling_hengsheng",
        "kb_id": "mock-yunling-hengsheng",
        "kb_name": "mock/云岭恒晟",
        "as_of": "2026-08-18",
    },
    "mock001": {
        "label": "泰锐（拒绝授信）",
        "exit": "拒绝授信",
        "kind": "mock",
        "subject": "东莞市泰锐精密传动件有限公司",
        "business_type": "",
        "collection": "mock_tairui_dd",
        "kb_id": "mock-tairui-dd",
        "kb_name": "mock/泰锐",
        "as_of": "2026-08-10",
    },
    # ---- 匿名化真实案例（2026-08-24 新增）----
    #
    # 由真实上市公司公开材料改名而来（见 eval/anonymize_case.py），
    # 财务数值真实、主体标识全部替换，并配了完整企业档案
    # （见 eval/build_anon_profiles.py）。
    #
    # **它们存在的意义**：在此之前，A 层从未在「数据充分」的真实材料上
    # 产出过评级——真实案例全部落在「数据不足」，而那只验证了拒绝路径。
    # 评分权重、三口径额度测算、结论段、跨层判定在有已核实字段时的行为，
    # 一次都没被走过。
    #
    # 案例包自带参考风险判断，可用于**事后对照**；
    # 它不进输入（照着目标等级配档案 = 拿答案倒推）。
    "anon04": {
        "label": "骏昇机械（健康制造）",
        "exit": "待观测",
        "kind": "anon",
        "subject": "浙江骏昇机械股份有限公司",
        "business_type": "factoring",
        "collection": "anon_case_04",
        "kb_id": "anon-case_04",
        "kb_name": "anon/骏昇机械",
        "as_of": "2025-06-30",
    },
    "anon12": {
        "label": "泓瑞集团（连亏+违规担保+子公司重整）",
        "exit": "待观测",
        "kind": "anon",
        "subject": "江苏泓瑞集团股份有限公司",
        "business_type": "supply_chain_finance",
        "collection": "anon_case_12",
        "kb_id": "anon-case_12",
        "kb_name": "anon/泓瑞集团",
        "as_of": "2024-05-31",
    },
    "anon11": {
        "label": "晟康药业（虚假陈述+重整+非标意见）",
        "exit": "待观测",
        "kind": "anon",
        "subject": "晟康药业股份有限公司",
        "business_type": "factoring",
        "collection": "anon_case_11",
        "kb_id": "anon-case_11",
        "kb_name": "anon/晟康药业",
        "as_of": "2022-05-31",
    },
    "case01": {
        "label": "case_01（数据不足）",
        "exit": "数据不足",
        "kind": "real_case",
        "case_id": "case_01",
    },
}

#: 尚未具备的样本，写在代码里而不是只写在计划里——
#: 跑观察的人应当在启动时就看见样本是不全的。
#:
#: 三类 A 层出口现在各有一个带语料的主体，但 9.3 要的是**每类两个**。
#: 单个主体测不出"这一类出口的不一致形态"，只能测出"这一个主体的形态"。
MISSING_EXITS = [
    "每类 A 层出口只有 1 个主体，9.3 要求 2 个（共 6 个，现有 3 个）",
]


def _mock_query(entry: Dict[str, Any]) -> str:
    """构造查询。**场景由档案的 business_type 决定，不硬编码**——

    泰锐不是保理业务，硬塞 factoring 会给它挂上 14 项永远无法核实的
    应收账款字段，凭空拉低核实率并污染观察基线（BC-58 记的正是
    "猜错场景会给报告加上一批永远查不到的字段"）。
    """
    subject, as_of = entry["subject"], entry["as_of"]
    scenario = entry.get("business_type") or ""
    kind = "应收账款保理贷前尽职调查" if scenario == "factoring" else "贷前尽职调查"
    line = f"业务类型：{scenario}；" if scenario else ""
    return (
        f"请对{subject}开展{kind}。{line}"
        f"研究截止日：{as_of}。只允许使用企业档案与本地知识库材料，不搜索互联网。"
        "请输出主体识别、股权与实际控制人、经营与财务、司法合规、关联与担保、"
        "舆情、风险等级、授信额度建议与是否需要人工复核，并为关键结论标注来源。"
    )


def _resolve(entry: Dict[str, Any]) -> Dict[str, Any]:
    """把主体表条目展开成一次运行需要的全部参数。"""
    if entry["kind"] in ("mock", "anon"):
        return {
            "query": _mock_query(entry),
            "subject_name": entry["subject"],
            "business_type": entry["business_type"],
            "as_of": entry["as_of"],
            "scope": [{"collection": entry["collection"], "kb_id": entry["kb_id"],
                       "kb_name": entry["kb_name"], "document_count": 1}],
        }
    from real_case_rag import scope_for_case  # noqa: WPS433
    from run_real_case_agent import _build_query  # noqa: WPS433

    case_dir = BACKEND / "eval" / "real_cases_processed" / entry["case_id"]
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "query": _build_query(manifest),
        "subject_name": str((manifest.get("subject") or {}).get("legal_name") or ""),
        "business_type": str(manifest.get("primary_business_type") or ""),
        "as_of": str(manifest.get("research_cutoff") or ""),
        "scope": scope_for_case(entry["case_id"]),
    }


async def run_once(key: str, entry: Dict[str, Any], index: int,
                   out_root: Path) -> Dict[str, Any]:
    from config.verification_policy import POLICY
    from service.deep_research_v2.service import DeepResearchV2Service

    # 观察期专用：关掉生产人工中断，否则每一轮都会停在卡点等人。
    # 报告仍必须自行判断"是否需要人工复核"——那是结论，不是流程开关。
    POLICY.require_human_review_gate = False

    params = _resolve(entry)
    session_id = f"stage2-{key}-r{index}-{uuid.uuid4().hex[:8]}"
    run_dir = out_root / session_id
    run_dir.mkdir(parents=True, exist_ok=True)

    service = DeepResearchV2Service(max_iterations=1)
    final_event: Dict[str, Any] | None = None
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as fh:
        async for sse in service.research(
            query=params["query"], session_id=session_id, user_id=None,
            search_web=False, search_local=True, max_iterations=0,
            subject_name=params["subject_name"],
            business_type=params["business_type"],
            due_diligence=True, as_of=params["as_of"], kb_scope=params["scope"],
            # ⚠️ 与封闭评测相反：本期的全部意义就在这个开关上。
            investigation=True,
        ):
            for payload in re.findall(r"^data:\s*(.+)$", sse, flags=re.MULTILINE):
                if payload == "[DONE]":
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                if event.get("type") == "research_complete":
                    final_event = event

    (run_dir / "result.json").write_text(json.dumps(
        {"session_id": session_id, "subject_key": key,
         "subject": params["subject_name"], "a_layer_exit": entry["exit"],
         "round": index, "status": "completed" if final_event else "failed",
         "started_at": datetime.now(timezone.utc).isoformat(),
         # 这批数据是在哪个代码状态下跑出来的——不靠人记。
         "code_rev": _code_rev(), "batch": out_root.name,
         "final_event": final_event},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_dir": run_dir, "final_event": final_event}


def _summary_line(key: str, index: int, result: Dict[str, Any]) -> str:
    event = result.get("final_event") or {}
    if not event:
        return f"  [{key} r{index}] 失败：没有终局事件"
    assessment = event.get("risk_assessment") or {}
    verdict = event.get("cross_layer_verdict") or {}
    counts = verdict.get("counts") or {}
    # 语料读数必须上摘要行。BC-75 之所以跑满 15 轮才被发现，就是因为
    # "留了几片、丢了几片"从来没有出现在任何一行输出里——
    # 静默丢掉 6/7 的语料，外观与"这份材料里没东西"完全相同（BC-51）。
    drops = event.get("investigation_corpus_drops") or []
    retained = max((int(d.get("retained") or 0) for d in drops), default=None)
    chars = max((int(d.get("retained_chars") or 0) for d in drops), default=None)
    over_budget = sum(int(d.get("dropped_over_budget") or 0) for d in drops)
    dup = sum(int(d.get("dropped_duplicate") or 0) for d in drops)
    box = event.get("investigation") or {}
    # ⚠️ `failures` 是**统一留痕列表**，不是失败列表（BC-51）。
    #    `not_found`（「查了没有」）是正常产出且占绝大多数——
    #    第二批 15 轮里 86 条 not_found、2 条 error。
    #    数 len(failures) 会让每一轮都挂着「抽取失败×3」，
    #    真出一次 error 反而淹没在里面。**恒亮的灯不是灯。**
    fails = len([f for f in (box.get("failures") or [])
                 if str(f.get("kind")) == "error"])
    corpus = ("｜语料=未记录" if retained is None else
              f"｜语料={retained}片/{chars}字（重复{dup} 超预算{over_budget}）")
    return (f"  [{key} r{index}] 等级={assessment.get('level') or '—'}"
            f"｜发现={counts.get('findings', 0)}"
            f"｜挑战={counts.get('challenges', 0)}"
            f"｜判定={verdict.get('verdict') or '—'}"
            + corpus
            + (f"｜⚠️抽取失败×{fails}" if fails else ""))


async def main_async(keys: List[str], repeats: int, out_root: Path) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"观察期运行：{len(keys)} 个主体 × {repeats} 轮 → {out_root}")
    rev = _code_rev()
    print(f"  代码版本：{rev}")
    if rev.endswith("-dirty"):
        print("  ⚠️ 工作区未提交——sha 描述不了跑的是什么代码，"
              "改动清单见 batch_meta.json")
    _write_batch_meta(out_root, keys, repeats)
    for gap in MISSING_EXITS:
        print(f"  ⚠️ 样本缺口：{gap}")
    print()

    failures = 0
    for key in keys:
        entry = SUBJECTS[key]
        for index in range(1, repeats + 1):
            try:
                result = await run_once(key, entry, index, out_root)
            except Exception as exc:  # 单轮失败不中断整批——观察期要的是样本量
                failures += 1
                print(f"  [{key} r{index}] 异常：{type(exc).__name__}: {exc}")
                continue
            if not (result.get("final_event") or {}):
                failures += 1
            print(_summary_line(key, index, result))

    print(f"\n完成。失败 {failures} 轮。")
    print(f"聚合：python eval/aggregate_cross_layer.py --runs {out_root}")
    if out_root != OUTPUT_ROOT:
        print("  ⚠️ 本批不在缺省聚合路径下——**不要与其他批次混算**，"
              "两批的代码状态不同，混算出来的是两个输入条件的平均数。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subjects", nargs="*", default=None,
                        help=f"主体键；缺省全部：{', '.join(SUBJECTS)}")
    parser.add_argument("--repeats", type=int, default=5,
                        help="每个主体重复轮次（9.3 预设 5，用于分离模型随机性）")
    parser.add_argument("--list", action="store_true", help="只打印主体表")
    parser.add_argument("--out-root", type=Path, default=OUTPUT_ROOT,
                        help="本批产物目录；缺省与历史批次同目录。"
                             "换了代码状态就换一个目录——混批是静默的")
    args = parser.parse_args()

    if args.list:
        for key, entry in SUBJECTS.items():
            print(f"{key:10} {entry['label']:22} A 层出口：{entry['exit']}")
        for gap in MISSING_EXITS:
            print(f"{'—':10} 缺口：{gap}")
        return 0

    keys = args.subjects or list(SUBJECTS)
    unknown = [k for k in keys if k not in SUBJECTS]
    if unknown:
        print(f"未知主体：{unknown}；可用：{list(SUBJECTS)}")
        return 1
    return asyncio.run(main_async(keys, args.repeats, args.out_root))


if __name__ == "__main__":
    raise SystemExit(main())
