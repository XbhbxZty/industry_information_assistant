# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
本地知识库检索范围与隔离契约（P0-1）

## 起因

本地检索一直是**死的**：写入侧 `knowledge_router.py:95` 用 `kb_<知识库名>`，
读取侧 `scout.py` 写死查 `"knowledge_base"`，而那个集合从不存在。
`milvus_service.search()` 第一行 `has_collection` 检查失败就 `return []`，
所以 `search_local=True` 时本地检索永远返回空——且走正常返回路径，
前端看不出任何区别。

## 但这不是"改个字符串"

"搜索所有知识库"意味着枚举集合，天真地枚举会把死链换成真正的跨用户泄漏：

| 事实 | 位置 |
|---|---|
| 集合名 = `kb_<kb.name>` | knowledge_router.py:95 |
| `kb.name` 只在单个用户内唯一 | knowledge_router.py:145-148 |
| Milvus schema 无 user_id | milvus_service.py:60-66 |
| `kb_id` 写的是集合名而非知识库 UUID | docmind_service.py:337 |

因此本轮的实质是**定契约**，修 bug 是副产品：范围一律从 PostgreSQL 按
登录用户解析，检索层不拼装集合名。

## 本文件不断言什么

集合物理层面的同名共用问题**没有修**（需要重建索引，见 kb_scope.py
「尚未修复的部分」）。这里断言的是读取路径的范围解析与故障语义，
不是"两个用户的切片已经分开存了"。

运行：cd backend && python tests/test_kb_scope.py
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from service.kb_scope import (  # noqa: E402
    KbScope, classify_missing_collection, collection_name_for, resolve_kb_scope,
)


class _FakeKb:
    def __init__(self, name, user_id, document_count=1):
        self.id = uuid.uuid4()
        self.name = name
        self.user_id = user_id
        self.document_count = document_count


class _FakeQuery:
    """最小 SQLAlchemy 查询替身：只支持本模块用到的 filter/all。"""

    def __init__(self, rows):
        self._rows = rows
        self._filters = []

    def filter(self, *conditions):
        self._filters.extend(conditions)
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows, raise_on_query=None):
        self._rows = rows
        self._raise = raise_on_query
        self.queried = False

    def query(self, model):
        self.queried = True
        if self._raise:
            raise self._raise
        return _FakeQuery(self._rows)


# ------------------------------------------------------- 一、命名基于 UUID

def test_collection_name_is_derived_from_uuid():
    """
    集合名必须由知识库 UUID 生成（BC-53）。Milvus 集合名只接受
    `[A-Za-z_][A-Za-z0-9_]*`，UUID 的连字符非法，因此取 .hex。
    """
    kb_id = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
    assert collection_name_for(kb_id) == "kb_550e8400e29b41d4a716446655440000"
    # 传字符串形式应当等价
    assert collection_name_for(str(kb_id)) == collection_name_for(kb_id)


def test_collection_name_is_valid_milvus_identifier():
    import re
    name = collection_name_for(uuid.uuid4())
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name), name
    assert len(name) <= 255


def test_collection_name_rejects_a_kb_name():
    """
    按知识库**名**构造集合名正是 BC-53 的成因。传名字必须当场报错，
    而不是生成一个看起来能用、实际不唯一的集合名。
    """
    for bad in ("财报", "Annual Report", "", None):
        try:
            collection_name_for(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} 不是 UUID，应当被拒绝")


def test_legacy_name_helper_preserves_old_rule():
    """迁移脚本要靠它找到旧数据，规则必须与历史写入侧逐字一致。"""
    from service.kb_scope import legacy_collection_name_for
    assert legacy_collection_name_for("财报") == "kb_财报"
    assert legacy_collection_name_for("Annual Report") == "kb_annual_report"


def test_same_name_different_users_get_different_collections():
    """
    ⭐ BC-53 的核心断言：两个用户各建一个「财报」，必须落在不同集合。
    旧方案下这两个知识库共用 `kb_财报`，切片物理混存。
    """
    a, b = _FakeKb("财报", "u1"), _FakeKb("财报", "u2")
    assert collection_name_for(a.id) != collection_name_for(b.id)


def test_rename_does_not_change_collection():
    """改名不再产生孤儿集合——集合名跟 UUID 走，与显示名无关。"""
    kb = _FakeKb("财报", "u1")
    before = collection_name_for(kb.id)
    kb.name = "2025年财报"
    assert collection_name_for(kb.id) == before


def test_recreated_kb_with_same_name_gets_new_collection():
    """
    删掉「财报」再建一个「财报」，不得继承旧向量。
    旧方案下新知识库会直接复用同名集合，旧文档原样复活。
    """
    old, new = _FakeKb("财报", "u1"), _FakeKb("财报", "u1")
    assert collection_name_for(old.id) != collection_name_for(new.id)


# ------------------------------------------------------ 二、空范围必须带原因

def test_empty_scope_requires_a_reason():
    """
    没有原因的空范围，下游无法区分"范围内没材料"与"没解析出范围"——
    正是 SearchOutcome 那条纪律在另一层的复现。
    """
    try:
        KbScope([])
    except ValueError:
        return
    raise AssertionError("空范围不带 failure_reason 应当被拒绝")


def test_missing_user_id_yields_empty_scope_not_open_access():
    """身份缺失时必须收敛为空范围，绝不能放行成"查全部"。"""
    scope = resolve_kb_scope(_FakeDb([]), user_id=None)
    assert not scope.entries
    assert "用户身份" in scope.failure_reason


def test_missing_db_yields_empty_scope():
    scope = resolve_kb_scope(None, user_id="u1")
    assert not scope.entries and scope.failure_reason


def test_db_exception_becomes_failure_not_silent_empty():
    scope = resolve_kb_scope(_FakeDb([], raise_on_query=RuntimeError("连接断了")),
                             user_id="u1")
    assert not scope.entries
    assert "RuntimeError" in scope.failure_reason, \
        "库挂了要说是库挂了，不能表现为『这个用户没有知识库』"


def test_user_without_kb_is_reported_as_such():
    scope = resolve_kb_scope(_FakeDb([]), user_id="u1")
    assert not scope.entries
    assert "没有任何知识库" in scope.failure_reason


def test_named_kb_not_found_says_so():
    scope = resolve_kb_scope(_FakeDb([]), user_id="u1", kb_name="不存在的库")
    assert "不存在的库" in scope.failure_reason


# ------------------------------------------------------------ 三、正常解析

def test_scope_carries_identity_for_each_kb():
    kbs = [_FakeKb("财报", "u1", 3), _FakeKb("合同", "u1", 0)]
    scope = resolve_kb_scope(_FakeDb(kbs), user_id="u1")
    assert scope.collections == [collection_name_for(kb.id) for kb in kbs]
    for e, kb in zip(scope.entries, kbs):
        assert e["kb_id"] == str(kb.id), "必须带知识库 UUID，供召回结果标注出处"
        assert e["kb_name"] == kb.name
    assert scope.entries[0]["document_count"] == 3


def test_scope_is_serializable_for_checkpoint():
    """范围要随 ResearchState 进检查点，必须是纯 dict。"""
    scope = resolve_kb_scope(_FakeDb([_FakeKb("财报", "u1")]), user_id="u1")
    import json
    json.loads(json.dumps(scope.as_state()))


# ------------------------------- 四、集合缺失：故障还是本来就没材料

def test_missing_collection_with_documents_is_a_failure():
    """
    登记有文档却查不到集合 = 索引丢失或改名产生孤儿集合。
    当成空结果处理，报告就会写"本地材料未见相关记录"——又一次把故障写成结论。
    """
    broken, reason = classify_missing_collection(
        {"kb_name": "财报", "collection": "kb_财报", "document_count": 5})
    assert broken
    assert "5" in reason and "kb_财报" in reason


def test_missing_collection_without_documents_is_not_a_failure():
    broken, _ = classify_missing_collection(
        {"kb_name": "空库", "collection": "kb_空库", "document_count": 0})
    assert not broken, "还没上传过文档，集合不存在是正常的"


# --------------------------------------------- 五、Scout 侧的范围与故障语义

def _scout():
    from service.deep_research_v2.agents.scout import DeepScout
    scout = DeepScout(llm_api_key="k", llm_base_url="http://localhost:1/v1",
                      search_api_key="k")
    scout.embedding_fn = lambda _query: [0.0, 1.0]
    return scout


class _FakeMilvus:
    def __init__(self, existing=(), hits=None):
        self.existing = set(existing)
        self.hits = hits or {}
        self.searched = []

    def has_collection(self, name):
        return name in self.existing

    def search(self, collection_name, query_vector, top_k=5, kb_id=None):
        self.searched.append(collection_name)
        return self.hits.get(collection_name, [])


def _run_local(scout, **kw):
    return asyncio.run(scout._execute_local_search("测试查询", **kw))


def test_empty_scope_is_a_failure_not_an_empty_result():
    """
    本文件最重要的一条。没有可查的知识库 ≠ 查了没有。
    返回空成功，报告就会写"本地材料未发现相关内容"——
    而实际上一份材料都没查过。
    """
    s = _scout()
    s.milvus_service = _FakeMilvus()
    out = _run_local(s, kb_scope=[])
    assert not out.ok
    assert "未解析到可检索的知识库范围" in out.failure_reason


def test_search_only_touches_collections_in_scope():
    """检索层不得自行扩大范围——它没有做授权判断的信息。"""
    s = _scout()
    s.milvus_service = _FakeMilvus(existing=("kb_甲", "kb_乙"))
    _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "1", "kb_name": "甲", "document_count": 1},
    ])
    assert s.milvus_service.searched == ["kb_甲"], \
        f"只应查范围内的集合，实际查了 {s.milvus_service.searched}"


def test_broken_index_fails_whole_search_not_partial_success():
    """
    部分知识库查成功，不足以让整次检索算成功：报告会据此写
    "本地材料未见相关记录"，而那部分材料根本没查过。
    """
    s = _scout()
    s.milvus_service = _FakeMilvus(existing=("kb_甲",))
    out = _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "1", "kb_name": "甲", "document_count": 1},
        {"collection": "kb_乙", "kb_id": "2", "kb_name": "乙", "document_count": 9},
    ])
    assert not out.ok
    assert "乙" in out.failure_reason and "索引丢失" in out.failure_reason


def test_empty_kb_without_documents_does_not_fail_the_search():
    s = _scout()
    s.milvus_service = _FakeMilvus(existing=("kb_甲",),
                                   hits={"kb_甲": [{"doc_id": "d1", "content": "x",
                                                    "filename": "f", "score": 0.9}]})
    out = _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "1", "kb_name": "甲", "document_count": 1},
        {"collection": "kb_空", "kb_id": "2", "kb_name": "空", "document_count": 0},
    ])
    assert out.ok, f"没上传文档的知识库不该拖垮整次检索：{out.failure_reason}"
    assert len(out.results) == 1


def test_results_carry_source_kb_identity():
    """
    报告里"本地知识库"四个字无法告诉复核人这条材料是谁提交的。
    尽调场景下材料出处直接影响可信度分级（企业自报 vs 审计报告）。
    """
    s = _scout()
    s.milvus_service = _FakeMilvus(
        existing=("kb_甲",),
        hits={"kb_甲": [{"doc_id": "d1", "content": "内容", "filename": "f.pdf",
                         "score": 0.8}]})
    out = _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "kbid-1", "kb_name": "甲", "document_count": 1},
    ])
    r = out.results[0]
    assert r["kb_id"] == "kbid-1" and r["kb_name"] == "甲"
    assert "甲" in r["site_name"]
    assert r["url"].startswith("local://kb/kbid-1/")


def test_cross_kb_results_are_merged_by_score():
    """
    不按相似度排序就是"先解析到的知识库优先"，与相关性无关。
    """
    s = _scout()
    s.milvus_service = _FakeMilvus(
        existing=("kb_甲", "kb_乙"),
        hits={
            "kb_甲": [{"doc_id": "a", "content": "低分", "filename": "a", "score": 0.2}],
            "kb_乙": [{"doc_id": "b", "content": "高分", "filename": "b", "score": 0.9}],
        })
    out = _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "1", "kb_name": "甲", "document_count": 1},
        {"collection": "kb_乙", "kb_id": "2", "kb_name": "乙", "document_count": 1},
    ], top_k=5)
    assert [r["doc_id"] for r in out.results] == ["b", "a"]


def test_milvus_unavailable_is_a_failure():
    s = _scout()
    s.milvus_service = None
    out = _run_local(s, kb_scope=[
        {"collection": "kb_甲", "kb_id": "1", "kb_name": "甲", "document_count": 1}])
    assert not out.ok and "Milvus" in out.failure_reason


# ------------------------------------------- 六、切片身份来自数据库主键

def test_chunk_identity_comes_from_db_keys_not_filename():
    """
    ⭐ 旧实现：
        doc_id = md5(文件名)
        id     = md5(f"{文件名}_{i}_{内容前50}")

    两个用户上传同名同内容的文件，会生成**完全相同的 Milvus 主键**，
    在共用集合里互相覆盖。改为数据库主键后两者天然不碰撞。
    """
    from service.docmind_service import process_document_with_docmind
    import inspect
    src = inspect.getsource(process_document_with_docmind)
    assert 'hashlib.md5(file_name.encode())' not in src, \
        "doc_id 不得再由文件名派生"
    assert '"kb_id": index_name' not in src, \
        "kb_id 不得再写集合名——在集合 X 里筛 kb_id == X 没有任何隔离作用"
    assert 'f"{document_id}_{i}"' in src, "切片主键须由文档 UUID 与序号构成"


def test_missing_ids_are_rejected_not_defaulted():
    """
    缺 kb_id / document_id 时必须失败，不能退回到按文件名派生——
    那正是要消除的行为。
    """
    from service.docmind_service import process_document_with_docmind
    r = process_document_with_docmind(
        file_path="/nonexistent", file_name="x.pdf",
        index_name="kb_x", kb_id="", document_id="",
    )
    assert not r["success"]


# ------------------------------------------------------- 七、迁移计划分类

def test_migration_refuses_to_split_shared_collections():
    """
    ⭐ 跨用户同名的旧集合**不能自动迁移**：旧切片不携带任何可追溯到
    具体知识库/用户的标识（doc_id 曾是 md5(文件名)），按内容无法拆分。

    猜错的后果是把 A 的财报搬进 B 的知识库——比丢数据严重。
    """
    from scripts.migrate_kb_collections import Plan, build_plans

    a, b = _FakeKb("财报", "u1"), _FakeKb("财报", "u2")
    solo = _FakeKb("合同", "u1")
    milvus = _FakeMilvus(existing=("kb_财报", "kb_合同"))
    plans = {p.kb_id: p for p in build_plans(_FakeDb([a, b, solo]), milvus)}

    assert plans[str(a.id)].status == Plan.AMBIGUOUS
    assert plans[str(b.id)].status == Plan.AMBIGUOUS
    assert plans[str(solo.id)].status == Plan.OK, \
        "只有一个知识库映射到的旧集合可以安全搬运"
    assert plans[str(a.id)].shared_with, "必须列出共用者，否则用户不知道该重建哪些"


def test_migration_skips_when_target_exists():
    from scripts.migrate_kb_collections import Plan, build_plans
    kb = _FakeKb("财报", "u1")
    milvus = _FakeMilvus(existing=("kb_财报", collection_name_for(kb.id)))
    plan = build_plans(_FakeDb([kb]), milvus)[0]
    assert plan.status == Plan.ALREADY_DONE, "重复执行迁移必须幂等"


def test_migration_reports_kb_without_legacy_data():
    from scripts.migrate_kb_collections import Plan, build_plans
    kb = _FakeKb("新库", "u1")
    plan = build_plans(_FakeDb([kb]), _FakeMilvus(existing=()))[0]
    assert plan.status == Plan.NO_LEGACY


# --------------------------------------------- 八、旧集合何时可以安全删除

class _CountingMilvus(_FakeMilvus):
    """按集合返回可控行数，用于验证删除前的取证。"""

    def __init__(self, counts):
        super().__init__(existing=tuple(counts))
        self.counts = counts

    def iter_all_rows(self, collection_name, batch_size=1000, include_vector=True):
        return [{"id": str(i)} for i in range(self.counts.get(collection_name, 0))]


def _plan_for(kb, status):
    from scripts.migrate_kb_collections import Plan
    return Plan(kb, status)


def test_drop_legacy_works_in_the_documented_two_step_flow():
    """
    ⭐ 真实环境跑出来的 bug（BC-54）。

    脚本文档推荐的是两步：先 `--apply` 迁移、确认检索正常后再
    `--apply --drop-legacy` 删旧。但第二次执行时目标集合已存在，
    计划状态是 ALREADY_DONE 而非 OK，`moved` 恒为 0——
    原判据 `status == OK and moved > 0` 恒假，**永远删不掉任何东西**。

    单测（只测分类）和空环境 dry-run 都没抓到，真实端到端抓到了。
    """
    from scripts.migrate_kb_collections import Plan, can_drop_legacy
    kb = _FakeKb("合同", "u1")
    plan = _plan_for(kb, Plan.ALREADY_DONE)
    m = _CountingMilvus({plan.legacy: 5, plan.target: 5})
    ok, why = can_drop_legacy(m, plan)
    assert ok, f"两步工作流的第二步必须能真正删除旧集合：{why}"


def test_drop_legacy_refuses_when_target_has_fewer_rows():
    """
    行数变少说明有切片没搬过去（文件名缺失或重名）。
    此时删旧集合就是丢数据，且不可逆。
    """
    from scripts.migrate_kb_collections import Plan, can_drop_legacy
    kb = _FakeKb("合同", "u1")
    plan = _plan_for(kb, Plan.OK)
    m = _CountingMilvus({plan.legacy: 10, plan.target: 7})
    ok, why = can_drop_legacy(m, plan)
    assert not ok and "未搬运" in why


def test_drop_legacy_never_touches_shared_collection():
    from scripts.migrate_kb_collections import Plan, can_drop_legacy
    kb = _FakeKb("财报", "u1")
    plan = _plan_for(kb, Plan.AMBIGUOUS)
    m = _CountingMilvus({plan.legacy: 8, plan.target: 8})
    ok, why = can_drop_legacy(m, plan)
    assert not ok and "共用" in why, "共用集合含他人数据，任何情况下都不能删"


def test_drop_legacy_refuses_when_target_missing():
    """迁移没跑就来删旧集合 = 直接丢数据。"""
    from scripts.migrate_kb_collections import Plan, can_drop_legacy
    kb = _FakeKb("合同", "u1")
    plan = _plan_for(kb, Plan.OK)
    m = _CountingMilvus({plan.legacy: 5})
    ok, why = can_drop_legacy(m, plan)
    assert not ok and "新集合不存在" in why


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
