# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
知识库集合迁移：`kb_<知识库名>` → `kb_<知识库UUID>`（BC-53）

## 为什么要迁移

旧命名把隔离边界建在 `kb.name` 上，而这个名字：

- **不唯一** —— 唯一性检查带 `user_id` 条件，只在单个用户内唯一。
  两个用户各建一个「财报」，切片进同一个 `kb_财报` 集合
- **可变**   —— 改名后新集合不存在，旧集合再没人查（改名即丢数据）
- **可复用** —— 删掉「财报」再建一个「财报」，旧向量原样复活

## 一个无法回避的硬限制

**跨用户混存的集合无法按内容拆分。**

旧的切片身份是 `doc_id = md5(文件名)`、`id = md5(f"{文件名}_{i}_{内容前50}")`
（`docmind_service.py`），完全由文件名和内容决定，**不携带任何能追溯到
具体知识库或用户的标识**。同名文件在两个用户之间甚至会产生相同主键，
后写的直接覆盖先写的。

因此本脚本对两种情形区别对待：

| 旧集合的映射情况 | 处理 |
|---|---|
| 恰好一个知识库映射到它 | 逐行搬运（含向量）到新集合，改写 kb_id / doc_id / id |
| 多个知识库映射到它 | **拒绝迁移**，列出受影响知识库，要求从源文件重建 |
| 集合不存在 | 跳过（该知识库尚无索引数据） |

拒绝而不是猜，是因为猜错的后果是把 A 的财报搬进 B 的知识库——
比丢数据严重得多。源文件保留在 `Document.file_path`，重建是可行的。

## doc_id 的重建方式

搬运时需要把 `doc_id` 从 `md5(文件名)` 改成文档 UUID。依据是
`filename` 字段：在**单个知识库内**按文件名回查 `Document` 表。
若同一知识库内有多个同名文档而无法区分，该文档标记为不可迁移，
其切片不搬（宁可缺，不可错配）。

## 用法

```bash
cd backend
# 1) 先看现状，不做任何修改
F:/conda_envs/dd-assistant/python.exe app/scripts/migrate_kb_collections.py

# 2) 确认报告无误后执行
F:/conda_envs/dd-assistant/python.exe app/scripts/migrate_kb_collections.py --apply

# 3) 迁移成功后再删除旧集合（默认保留，便于回滚）
F:/conda_envs/dd-assistant/python.exe app/scripts/migrate_kb_collections.py --apply --drop-legacy
```

**默认 dry-run**。这是不可逆操作，默认不做事是唯一可接受的默认值。
`--drop-legacy` 单独一步，是为了让"搬完了"和"删掉了"之间留一个可回滚的窗口。
"""
import argparse
import os
import sys
from collections import defaultdict
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.knowledge import Document, KnowledgeBase  # noqa: E402
from service.kb_scope import collection_name_for, legacy_collection_name_for  # noqa: E402
from service.milvus_service import get_milvus_service  # noqa: E402


class Plan:
    """单个知识库的迁移计划。"""

    OK = "ok"                      # 可搬运
    AMBIGUOUS = "ambiguous"        # 旧集合被多个知识库共用，拒绝迁移
    NO_LEGACY = "no_legacy"        # 旧集合不存在，无事可做
    ALREADY_DONE = "already_done"  # 新集合已存在

    def __init__(self, kb, status, detail="", shared_with=None):
        self.kb_id = str(kb.id)
        self.kb_name = kb.name
        self.user_id = str(kb.user_id)
        self.legacy = legacy_collection_name_for(kb.name)
        self.target = collection_name_for(kb.id)
        self.status = status
        self.detail = detail
        self.shared_with = shared_with or []
        self.moved = 0
        self.skipped = 0


def build_plans(db, milvus) -> List[Plan]:
    kbs = db.query(KnowledgeBase).all()

    # 先按旧集合名分组，找出跨知识库共用的那些
    by_legacy: Dict[str, List[Any]] = defaultdict(list)
    for kb in kbs:
        by_legacy[legacy_collection_name_for(kb.name)].append(kb)

    plans = []
    for kb in kbs:
        legacy = legacy_collection_name_for(kb.name)
        target = collection_name_for(kb.id)
        sharers = by_legacy[legacy]

        if milvus.has_collection(target):
            plans.append(Plan(kb, Plan.ALREADY_DONE,
                              f"目标集合 {target} 已存在，跳过"))
        elif not milvus.has_collection(legacy):
            plans.append(Plan(kb, Plan.NO_LEGACY,
                              f"旧集合 {legacy} 不存在（该知识库可能尚未上传文档）"))
        elif len(sharers) > 1:
            others = [f"{s.name}（user={s.user_id}）" for s in sharers if s.id != kb.id]
            plans.append(Plan(
                kb, Plan.AMBIGUOUS,
                f"旧集合 {legacy} 被 {len(sharers)} 个知识库共用，切片无法按内容归属",
                shared_with=others,
            ))
        else:
            plans.append(Plan(kb, Plan.OK, f"{legacy} → {target}"))
    return plans


def _doc_uuid_index(db, kb_id: str) -> Dict[str, Any]:
    """
    知识库内 文件名 → 文档 UUID。

    同名重复的文件名映射到 None：无法区分就不迁移那部分切片，
    宁可缺一份材料，也不能把切片挂到错误的文档上。
    """
    docs = db.query(Document).filter(Document.knowledge_base_id == kb_id).all()
    index: Dict[str, Any] = {}
    for d in docs:
        if d.filename in index:
            index[d.filename] = None      # 重名，标记为不可判定
        else:
            index[d.filename] = str(d.id)
    return index


def migrate_one(db, milvus, plan: Plan) -> None:
    rows = milvus.iter_all_rows(plan.legacy, include_vector=True)
    if not rows:
        plan.detail += "；旧集合为空，无需搬运"
        return

    name_to_uuid = _doc_uuid_index(db, plan.kb_id)
    payload = []
    unresolved = set()

    for r in rows:
        filename = r.get("filename") or ""
        doc_uuid = name_to_uuid.get(filename)
        if not doc_uuid:
            unresolved.add(filename or "(无文件名)")
            plan.skipped += 1
            continue
        idx = int(r.get("chunk_index") or 0)
        payload.append({
            "id": f"{doc_uuid}_{idx}",
            "doc_id": doc_uuid,
            "kb_id": plan.kb_id,
            "filename": filename,
            "content": r.get("content") or "",
            "chunk_index": idx,
            "vector": r.get("vector"),
        })

    if payload:
        milvus.insert_documents(plan.target, payload)
        plan.moved = len(payload)

    if unresolved:
        plan.detail += (
            f"；{plan.skipped} 条切片未搬运（文件名在 Document 表中缺失或重名，"
            f"无法确定所属文档）：{sorted(unresolved)[:5]}"
        )


def can_drop_legacy(milvus, plan: Plan) -> tuple:
    """
    该旧集合现在可以安全删除吗？

    ## 为什么不能用 `status == OK and moved > 0` 判断

    那是本脚本第一版的写法，真实环境跑下来发现：**推荐的两步工作流下
    它永远删不掉任何东西**。第二次执行时目标集合已存在，计划状态变成
    `ALREADY_DONE`，`migrate_one` 不会跑，`moved` 恒为 0，删除条件恒假。

    只有一次性 `--apply --drop-legacy` 才会真正删除——而那恰恰是脚本
    docstring 里建议**不要**用的方式。文档写的流程和代码的行为对不上，
    与 BC-49 / BC-50 同形，也是一次"单测全绿但真实路径不通"。

    ## 判据改为对当前状态取证，而不是对本次是否搬过取证

    删除是不可逆的，因此每个条件都要求**当场验证**，不依赖上一次执行的记忆：
    目标集合存在、行数不少于旧集合。行数变少说明有切片没搬过去
    （文件名在 Document 表中缺失或重名），此时删旧集合就是丢数据。

    Returns: (是否可删, 原因说明)
    """
    if plan.status == Plan.AMBIGUOUS:
        return False, "共用集合，含其它知识库的数据"
    if not milvus.has_collection(plan.legacy):
        return False, "旧集合不存在"
    if not milvus.has_collection(plan.target):
        return False, "新集合不存在，迁移尚未完成"

    legacy_n = len(milvus.iter_all_rows(plan.legacy, include_vector=False))
    target_n = len(milvus.iter_all_rows(plan.target, include_vector=False))
    if target_n < legacy_n:
        return False, (f"新集合 {target_n} 行少于旧集合 {legacy_n} 行，"
                       f"存在未搬运的切片，删除会丢数据")
    return True, f"新集合 {target_n} 行 ≥ 旧集合 {legacy_n} 行"


def report(plans: List[Plan], applied: bool) -> int:
    buckets: Dict[str, List[Plan]] = defaultdict(list)
    for p in plans:
        buckets[p.status].append(p)

    print("=" * 72)
    print("知识库集合迁移" + ("（已执行）" if applied else "（DRY RUN，未做任何修改）"))
    print("=" * 72)

    for p in buckets[Plan.OK]:
        mark = f"搬运 {p.moved} 条" if applied else "待搬运"
        print(f"  [可迁移] {p.kb_name}  {p.legacy} → {p.target}  {mark}")
        if p.detail and "；" in p.detail:
            print(f"           {p.detail.split('；', 1)[1]}")

    for p in buckets[Plan.ALREADY_DONE]:
        print(f"  [已完成] {p.kb_name}  {p.detail}")

    for p in buckets[Plan.NO_LEGACY]:
        print(f"  [无数据] {p.kb_name}  {p.detail}")

    blocked = buckets[Plan.AMBIGUOUS]
    if blocked:
        print()
        print("!" * 72)
        print("以下知识库**拒绝自动迁移**：旧集合被多个知识库共用，")
        print("而旧切片不携带任何可追溯到具体知识库/用户的标识，无法按内容拆分。")
        print("!" * 72)
        for p in blocked:
            print(f"  [需重建] {p.kb_name}（user={p.user_id}, kb={p.kb_id}）")
            print(f"           旧集合 {p.legacy}，同名的还有：{p.shared_with}")
        print()
        print("  处理方式：这些知识库需要**从源文件重新索引**。")
        print("  源文件保留在 Document.file_path，可重新走上传→解析流程。")
        print("  在重建完成之前，这些知识库的本地检索会报『索引丢失』故障，")
        print("  而不是静默返回空——这是有意的（BC-51/BC-52）。")

    print()
    print(f"合计：可迁移 {len(buckets[Plan.OK])}，已完成 {len(buckets[Plan.ALREADY_DONE])}，"
          f"无数据 {len(buckets[Plan.NO_LEGACY])}，需重建 {len(blocked)}")
    if not applied:
        print("\n这是 DRY RUN。确认无误后加 --apply 执行。")
    return len(blocked)


def main() -> int:
    ap = argparse.ArgumentParser(description="迁移知识库 Milvus 集合命名（BC-53）")
    ap.add_argument("--apply", action="store_true",
                    help="真正执行搬运。不加则只输出计划")
    ap.add_argument("--drop-legacy", action="store_true",
                    help="搬运成功后删除旧集合。建议先不加，确认检索正常后再单独跑一次")
    args = ap.parse_args()

    from core.database import SessionLocal

    db = SessionLocal()
    milvus = get_milvus_service()
    try:
        plans = build_plans(db, milvus)

        if args.apply:
            for p in plans:
                if p.status == Plan.OK:
                    migrate_one(db, milvus, p)

        blocked = report(plans, args.apply)

        if args.apply and args.drop_legacy:
            print("\n删除旧集合：")
            for p in plans:
                ok, why = can_drop_legacy(milvus, p)
                if ok:
                    milvus.delete_collection(p.legacy)
                    print(f"  已删除 {p.legacy}（{why}）")
                elif p.status != Plan.NO_LEGACY:
                    print(f"  保留 {p.legacy}：{why}")

        # 有需要重建的知识库时以非零码退出，便于脚本化流程察觉
        return 1 if blocked else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
