# Copyright © 2026 XbhbxZty
"""把泰锐（MOCK-001）虚构尽调材料包灌进独立的 Milvus 集合。

与 `ingest_mock_corpus.py` 同构：自己的 `mock_` 前缀 + 同样的删除护栏，
不复用是因为那个脚本的集合名、页眉、文件名全是 MOCK-002 的常量。

## 切片方式

按页切，每片带页眉与页码。片段头写入 `[mock=true; source_id=MOCK001;
locator=page:N]`——**主体确认走文档级标识**（`_document_identity_zone`
读的是片段头部 400 字 + 标题 + URL），所以页眉必须带公司全称，
否则整份语料会被主体确认闸门整体拒掉。

用法：
    python eval/ingest_tairui_corpus.py --dry-run
    python eval/ingest_tairui_corpus.py --replace
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

COLLECTION = "mock_tairui_dd"
KB_ID = "mock-tairui-dd"
DOC_ID = "mock-doc-tairui-2025"
FILENAME = "MOCK001_dd_package_2025.txt"
SOURCE = Path(__file__).resolve().parent / "mock_corpus" / FILENAME
HEADER = "东莞市泰锐精密传动件有限公司 贷前尽职调查材料包"


def split_pages(text: str) -> list[dict[str, Any]]:
    parts = [p for p in re.split(rf"(?={re.escape(HEADER)})", text) if p.strip()]
    rows = []
    for index, body in enumerate(parts):
        match = re.search(r"贷前尽职调查材料包\s*\n(\d+)", body)
        page = match.group(1) if match else str(index)
        rows.append({
            "id": f"{DOC_ID}-p{page}",
            "doc_id": DOC_ID,
            "kb_id": KB_ID,
            "filename": FILENAME,
            "content": f"[mock=true; source_id=MOCK001; locator=page:{page}]\n"
                       f"标题：{HEADER}\n发布日期：2026-08-10\n{body.strip()}",
            "chunk_index": index,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    if not SOURCE.exists():
        raise SystemExit(f"语料不存在，先跑 build_tairui_corpus.py：{SOURCE}")

    rows = split_pages(SOURCE.read_text(encoding="utf-8"))
    if not rows:
        raise SystemExit("切片为空——页眉与文档不匹配？")
    print(f"切出 {len(rows)} 片，平均 "
          f"{sum(len(r['content']) for r in rows) // len(rows)} 字符")

    # 主体确认闸门读片段头部：这里先自检，避免灌完才发现整份会被拒
    from service.rag_evidence_bridge import _subject_aliases
    aliases = _subject_aliases("东莞市泰锐精密传动件有限公司")
    bad = [r["id"] for r in rows
           if not any(a in r["content"][:400].replace(" ", "") for a in aliases)]
    if bad:
        raise SystemExit(f"这些片段头部认不出主体，灌进去也会被整体拒绝：{bad}")
    print(f"主体确认自检通过（别名 {aliases}）")

    if args.dry_run:
        for row in rows[:3]:
            print(f"  {row['id']}: {row['content'][:78]}...")
        return 0

    from service.embedding_service import generate_embedding
    from service.milvus_service import get_milvus_service

    milvus = get_milvus_service()
    if milvus.has_collection(COLLECTION):
        if not args.replace:
            raise SystemExit(f"集合已存在，需要 --replace：{COLLECTION}")
        # 与 ingest_mock_corpus 同构的护栏：只允许删自己前缀的集合
        if not COLLECTION.startswith("mock_"):
            raise SystemExit(f"拒绝删除非 mock_ 前缀的集合：{COLLECTION}")
        milvus.delete_collection(COLLECTION)
        print(f"已删除旧集合 {COLLECTION}")

    payload = [{**row, "vector": generate_embedding(row["content"])} for row in rows]
    inserted = milvus.insert_documents(COLLECTION, payload)
    print(f"入库完成：collection={COLLECTION} inserted={inserted} "
          f"stats={milvus.get_collection_stats(COLLECTION)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
