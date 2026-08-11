"""从 Critic JSONL 原始记录复算消融指标，不依赖终端抄数。

同时报告两种不能混为一谈的注入指标：
  target_hit       是否命中预先指定的违规类型/等价描述（原始记录的 ok）
  report_blocked   最终裁决是否阻止报告通过（verdict != pass）

前者用于评价检测器是否理解了目标失效，后者用于评价生产链路有没有放行坏报告。
模型可能以 hallucination 而非 conflict_silently_resolved 打回同一句话，此时
report_blocked=True、target_hit=False；只报其中一个都会夸大或低估系统能力。

用法：
  python eval/summarize_runs.py eval/runs/*_dev_*_v3.jsonl
"""
import argparse
import json
import os
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple


def _read(path: str) -> Tuple[Dict, List[Dict]]:
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if not rows or rows[0].get("_type") != "run_meta":
        raise ValueError(f"{path}: 首行不是 run_meta")
    meta, records = rows[0], rows[1:]
    expected = int(meta["cases"]) * int(meta["repeat"])
    if len(records) != expected:
        raise ValueError(f"{path}: 记录数 {len(records)}，预期 {expected}")
    return meta, records


def _pct(n: int, d: int) -> str:
    return "—" if not d else f"{n / d:.1%}"


def _stability(records: Iterable[Dict], kind: str) -> Tuple[int, int, int, int]:
    by_case: Dict[str, List[bool]] = defaultdict(list)
    for row in records:
        if row.get("kind") == kind and row.get("ok") is not None:
            by_case[row["case_id"]].append(bool(row["ok"]))
    stable_pass = stable_fail = unstable = 0
    for oks in by_case.values():
        if all(oks):
            stable_pass += 1
        elif not any(oks):
            stable_fail += 1
        else:
            unstable += 1
    return stable_pass, unstable, stable_fail, len(by_case)


def summarize(path: str) -> Dict:
    meta, rows = _read(path)
    invalid = [r for r in rows if not r.get("llm_ok") or r.get("degraded")]
    valid = [r for r in rows if r not in invalid and r.get("ok") is not None]
    injections = [r for r in valid if r.get("kind") == "injection"]
    clean = [r for r in valid if r.get("kind") == "clean"]
    target_hits = sum(bool(r["ok"]) for r in injections)
    blocked = sum(
        bool(r.get("operational_blocked", r.get("verdict") != "pass"))
        for r in injections
    )
    clean_ok = sum(bool(r["ok"]) for r in clean)
    elapsed = [float(r.get("elapsed_s", 0)) for r in valid]
    return {
        "file": os.path.basename(path), "meta": meta, "invalid": len(invalid),
        "inj_n": len(injections), "target_hits": target_hits, "blocked": blocked,
        "clean_n": len(clean), "clean_ok": clean_ok,
        "inj_stability": _stability(valid, "injection"),
        "clean_stability": _stability(valid, "clean"),
        "avg_s": sum(elapsed) / len(elapsed) if elapsed else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()
    results = [summarize(p) for p in args.paths]
    print("| 组 | 模型/消融 | 目标命中 | 坏报告拦截 | 对照无误报 | 误报率 | 稳定性(注入/对照) | LLM失败 | 均时 |")
    print("|---|---|---:|---:|---:|---:|---|---:|---:|")
    for r in results:
        m = r["meta"]
        si, ui, fi, ti = r["inj_stability"]
        sc, uc, fc, tc = r["clean_stability"]
        clean_fp = r["clean_n"] - r["clean_ok"]
        label = m.get("label") or r["file"]
        config = f"{m.get('model')} / {m.get('ablate')}"
        print(
            f"| {label} | {config} | "
            f"{r['target_hits']}/{r['inj_n']} ({_pct(r['target_hits'], r['inj_n'])}) | "
            f"{r['blocked']}/{r['inj_n']} ({_pct(r['blocked'], r['inj_n'])}) | "
            f"{r['clean_ok']}/{r['clean_n']} ({_pct(r['clean_ok'], r['clean_n'])}) | "
            f"{_pct(clean_fp, r['clean_n'])} | "
            f"{si}/{ui}/{fi} of {ti} / {sc}/{uc}/{fc} of {tc} | "
            f"{r['invalid']} | {r['avg_s']:.1f}s |"
        )
    return 2 if any(r["invalid"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
