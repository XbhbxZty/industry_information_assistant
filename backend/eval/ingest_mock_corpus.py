# Copyright © 2026 XbhbxZty
"""把虚构年报文档灌进一个独立的 Milvus 集合，供 RAG 路径端到端验证。

## 与 `ingest_real_case_rag.py` 的关系

刻意**另写一个**而不是复用：那个脚本的集合名由 `collection_for_case()` 从
`case_01..case_12` 派生，并且 `--replace` 前会断言集合名以 `eval_case_` 开头、
`_sources` 结尾——那道断言是防止误删生产集合的护栏，不该为了跑虚构数据把它
放宽。本脚本用自己的前缀 `mock_` 并保留同构的护栏。

## 切片方式

按页切，与真实年报入库口径一致：每片带页眉和页码，这样
`statement_scope` 的分节索引能按 chunk_index 定位「合并/母公司」分界点。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(BACKEND / "app"))
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

COLLECTION = "mock_yunling_hengsheng"
KB_ID = "mock-yunling-hengsheng"
DOC_ID = "mock-doc-annual-2025"
FILENAME = "MOCK002_annual_report_2025.txt"
SOURCE = Path(__file__).resolve().parent / "mock_corpus" / FILENAME
HEADER = "云岭恒晟精密机械有限公司 2025 年年度报告全文"


def split_pages(text: str) -> list[dict[str, Any]]:
    parts = [p for p in re.split(rf"(?={re.escape(HEADER)})", text) if p.strip()]
    rows = []
    for index, body in enumerate(parts):
        match = re.search(r"年年度报告全文\s*\n(\d+)", body)
        page = match.group(1) if match else str(index)
        rows.append({
            "id": f"{DOC_ID}-p{page}",
            "doc_id": DOC_ID,
            "kb_id": KB_ID,
            "filename": FILENAME,
            # 片段头与真实案例入库格式一致：来源可追溯，且主体确认走文档级
            "content": f"[mock=true; source_id=MOCK002; locator=page:{page}]\n"
                       f"标题：{HEADER}\n发布日期：2026-03-12\n{body.strip()}",
            "chunk_index": index,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    rows = split_pages(SOURCE.read_text(encoding="utf-8"))
    print(f"切出 {len(rows)} 片，平均 {sum(len(r['content']) for r in rows)//len(rows)} 字符")
    if args.dry_run:
        for r in rows[:3]:
            print(f"  {r['id']}: {r['content'][:70]}...")
        return 0

    from service.embedding_service import generate_embedding
    from service.milvus_service import get_milvus_service

    milvus = get_milvus_service()
    if milvus.has_collection(COLLECTION):
        if not args.replace:
            raise SystemExit(f"集合已存在，需要 --replace：{COLLECTION}")
        # 与 ingest_real_case_rag 同构的护栏：只允许删自己前缀的集合
        if not COLLECTION.startswith("mock_"):
            raise SystemExit(f"拒绝删除非 mock_ 前缀的集合：{COLLECTION}")
        milvus.delete_collection(COLLECTION)
        print(f"已删除旧集合 {COLLECTION}")

    payload = []
    for row in rows:
        payload.append({**row, "vector": generate_embedding(row["content"])})
    inserted = milvus.insert_documents(COLLECTION, payload)
    stats = milvus.get_collection_stats(COLLECTION)
    print(f"入库完成：collection={COLLECTION} inserted={inserted} stats={stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
