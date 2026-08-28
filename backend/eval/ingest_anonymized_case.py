# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""把匿名化后的案例语料灌进独立的 Milvus 集合

## 与 ingest_tairui_corpus 的关系

同构，不复用——那个脚本的集合名、页眉、文件名全是 MOCK-001 的常量，
而这里要处理三个来源不同的案例包。复用会把常量参数化成一坨，
反而更难读。

## 灌库前必须过主体确认自检

`_document_identity_zone` 读片段**头部 400 字**找主体别名。匿名化改的是
正文，如果某些片段头部原本就没有公司名（例如纯财务报表页），
灌进去之后整片会被证据闸门拒绝——而那时才发现就晚了，
一次嵌入的钱已经花掉。

泰锐那个脚本立的规矩是"认不出就直接退出"。这里**放宽为报告比例**：
真实年报有大量表格页天然不带公司名，全拒会一片都灌不进去。
但比例必须打印出来，它直接决定这份语料的核实率上限。

用法：
    python eval/ingest_anonymized_case.py --case case_11 --dry-run
    python eval/ingest_anonymized_case.py --all --replace
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

SRC_ROOT = BACKEND / "eval" / "anonymized_cases"

#: 集合名前缀。删除护栏只认这个前缀——与 mock_ 同一条纪律：
#: **脚本永远不许删不是自己建的集合**。
PREFIX = "anon_"


def load_case(case_id: str) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    meta = json.loads((SRC_ROOT / case_id / "anonymization.json")
                      .read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in
            (SRC_ROOT / case_id / "chunks.jsonl").read_text(encoding="utf-8")
            .splitlines() if l.strip()]
    return meta, rows


def identity_check(rows: List[Dict[str, Any]], subject: str) -> Dict[str, Any]:
    """逐片验证头部 400 字能否认出主体。

    返回比例而不是直接退出——真实年报有大量表格页天然不带公司名。
    但这个比例**直接决定该语料的核实率上限**，所以必须打印。
    """
    from service.rag_evidence_bridge import _subject_aliases

    aliases = _subject_aliases(subject)
    ok = 0
    for row in rows:
        head = str(row.get("content") or "")[:400].replace(" ", "")
        if any(a in head for a in aliases):
            ok += 1
    return {"aliases": aliases, "ok": ok, "total": len(rows),
            "rate": ok / max(1, len(rows))}


def run_case(case_id: str, dry: bool, replace: bool) -> int:
    meta, rows = load_case(case_id)
    subject = meta["fictional_name"]
    collection = f"{PREFIX}{case_id}"
    print(f"=== {case_id} → {subject}")
    print(f"    分片 {len(rows)}｜集合 {collection}")

    check = identity_check(rows, subject)
    print(f"    主体确认自检：{check['ok']}/{check['total']} "
          f"（{check['rate']:.0%}）片头部可认出主体")
    print(f"    别名：{check['aliases']}")
    if check["rate"] < 0.2:
        print("    ⛔ 可认出比例低于 20%，灌进去大半会被证据闸门拒掉。")
        print("       先查匿名化是否破坏了片段头部的公司名。")
        return 2
    print(f"    → 这个比例是该语料核实率的**上限约束**，记下来")

    if dry:
        print(f"    （--dry-run：不灌库）示例片段头部：")
        print(f"      {str(rows[0].get('content'))[:110]}...")
        return 0

    from service.embedding_service import generate_embedding
    from service.milvus_service import get_milvus_service

    milvus = get_milvus_service()
    if milvus.has_collection(collection):
        if not replace:
            print(f"    ⛔ 集合已存在，需要 --replace")
            return 1
        # 只许删自己前缀的集合——与 mock_ 那条护栏同一条纪律
        if not collection.startswith(PREFIX):
            print(f"    ⛔ 拒绝删除非 {PREFIX} 前缀的集合：{collection}")
            return 1
        milvus.delete_collection(collection)
        print(f"    已删除旧集合 {collection}")

    payload = []
    for i, row in enumerate(rows, 1):
        text = str(row.get("content") or "")
        payload.append({
            "id": str(row.get("id") or f"{case_id}-{i}"),
            "doc_id": str(row.get("doc_id") or f"anon-{case_id}"),
            "kb_id": f"anon-{case_id}",
            "filename": str(row.get("filename") or f"{case_id}.txt"),
            "content": text,
            "chunk_index": int(row.get("chunk_index") or i),
            "vector": generate_embedding(text),
        })
        if i % 100 == 0:
            print(f"      嵌入 {i}/{len(rows)}")
    inserted = milvus.insert_documents(collection, payload)
    print(f"    ✅ 入库 {inserted} 片 → {collection}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    ids = (sorted(p.name for p in SRC_ROOT.iterdir() if p.is_dir())
           if args.all else ([args.case] if args.case else []))
    if not ids:
        print("指定 --case 或 --all")
        return 1
    rc = 0
    for cid in ids:
        rc = run_case(cid, args.dry_run, args.replace) or rc
        print()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
