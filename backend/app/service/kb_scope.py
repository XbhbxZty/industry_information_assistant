# Copyright © 2026 XbhbxZty
# 本文件为「尽调智核」迭代中新增，不含原课程项目代码。
"""
本地知识库检索范围（P0-1）

## 起因：本地检索一直是死的

写入侧 `knowledge_router.process_document()` 把切片写进 `kb_<知识库名>`；
读取侧 `scout._execute_local_search()` 写死查 `"knowledge_base"`。
全仓库没有任何地方创建过叫 `knowledge_base` 的集合，而
`milvus_service.search()` 第一行就是：

    if not utility.has_collection(collection_name):
        print(...); return []

于是 `search_local=True` 时本地检索**永远返回空列表**，从 V2 上线至今
一次都没命中过。而且它走的是正常返回路径，不是异常——日志只有 milvus
层一句 print，前端完全看不出区别。

## 但改个集合名不是修复

"搜索所有知识库"在当前设计下意味着枚举集合，而天真地枚举会把死链换成
真正的跨用户泄漏。原因是 Milvus 侧**没有身份**：

| 事实 | 位置 |
|---|---|
| 集合名 = `kb_<kb.name>` | `knowledge_router.py:95` |
| `kb.name` 只在**单个用户内**唯一 | `knowledge_router.py:145-148` |
| Milvus schema 无 `user_id` 字段 | `milvus_service.py:60-66` |
| `kb_id` 写入的是集合名本身，不是知识库 UUID | `docmind_service.py:337` |

推论：**两个用户各建一个叫「财报」的知识库，切片进同一个 `kb_财报` 集合**，
而 `kb_id` 过滤形同虚设（在 `kb_财报` 里筛 `kb_id == "kb_财报"` 匹配全部）。

**✅ 已修复（BC-53）**：集合名改为 `kb_<知识库UUID>`，`kb_id` 改写为知识库 UUID，
`doc_id` 改写为文档 UUID。既有数据的迁移见
`scripts/migrate_kb_collections.py`。同一批修复顺带解决：

- 改名不再产生孤儿集合（UUID 不随名字变）
- 删除知识库后重建同名，不再继承旧向量（UUID 不复用）
- 不同用户的同名文件不再产生相同的 Milvus 主键（`doc_id` 曾是 `md5(文件名)`）
- 删除知识库/文档时同步清理向量（此前从不清理）

## 契约

1. **检索范围一律从 PostgreSQL 解析，不由查询期的名字拼装。**
   授权在关系库里（`KnowledgeBase.user_id`），向量库里没有，
   因此只有前者能回答"这个人能看哪些材料"。
2. **尽调场景下范围还要再收一层**：只搜本次尽调主体的材料。
   A 企业的财报被 B 企业的尽调召回，会直接写进 B 的报告并影响评级。
3. **解析不出范围是故障，不是"没有相关材料"。**
   与 `SearchOutcome` 同一条纪律：没得查 ≠ 查了没有。
4. **召回内容只进 facts，不翻转 field_check。**
   上传材料是企业自报（`_CREDIBILITY['self_reported'] = 0.60`），
   不是权威登记信息；字段级核实要走结构化适配器（verification 规则 2）。

## 迁移的一个硬限制

旧数据里**混存的集合无法按内容拆分**。原因是 `doc_id` 曾是
`md5(文件名)`（`docmind_service.py`），不是文档 UUID——切片本身
不携带任何能追溯到具体知识库或用户的标识。

因此迁移脚本对两种情形区别对待：

| 旧集合 | 处理 |
|---|---|
| 只有一个知识库映射到它 | 逐行搬运到新集合并改写 `kb_id`/`doc_id`（无需重新向量化） |
| 多个知识库映射到它（跨用户同名） | **拒绝迁移**，报告受影响的知识库，要求从源文件重建 |

拒绝而不是猜，是因为猜错的后果是把 A 的财报搬进 B 的知识库——
比丢数据严重。源文件保留在 `Document.file_path`，重建是可行的。

## 仍然依赖 document_count 的那条判断

`classify_missing_collection()` 保留：即便命名问题已解决，
"登记有文档但集合不存在"仍然是需要区分故障与空结果的情形
（迁移未跑、索引重建失败、Milvus 数据丢失）。
"""
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def collection_name_for(kb_id: Any) -> str:
    """
    知识库 UUID → Milvus 集合名。

    ## 为什么是 UUID 而不是知识库名（BC-53）

    旧方案 `kb_<kb.name>` 有三个致命性质：

    1. **不唯一**：`kb.name` 的唯一性检查带 `user_id` 条件，只在单个用户内唯一。
       两个用户各建一个「财报」，切片进同一个集合。
    2. **可变**：用户改名后新集合不存在，旧集合再没人查——改名即丢数据。
    3. **删除后可复用**：删掉「财报」再建一个「财报」，旧向量原样复活。

    UUID 一次性解决三条：全局唯一、不可变、不复用。

    ## 命名约束

    Milvus 集合名只接受 `[A-Za-z_][A-Za-z0-9_]*`，UUID 的连字符非法，
    因此取 `.hex`。`kb_` + 32 位十六进制 = 35 字符，远低于 255 上限。

    这个规则**有且只有这一处定义**。读写两侧各自维护同一个规则，
    是本地检索整条链路静默失效的直接成因（BC-52）。
    """
    if isinstance(kb_id, uuid.UUID):
        return f"kb_{kb_id.hex}"
    try:
        return f"kb_{uuid.UUID(str(kb_id)).hex}"
    except (ValueError, AttributeError, TypeError) as e:
        raise ValueError(
            f"集合名必须由知识库 UUID 生成，收到 {kb_id!r}。"
            f"按知识库**名**构造集合名是 BC-53 的成因，已不再支持"
        ) from e


def legacy_collection_name_for(kb_name: str) -> str:
    """
    旧方案的集合名（`kb_<知识库名>`）。

    **只供迁移脚本使用**，生产路径一律用 `collection_name_for()`。
    保留它是因为迁移必须能找到旧数据；保留在这里而不是散在脚本里，
    是为了让"旧规则长什么样"同样只有一处定义。
    """
    return f"kb_{kb_name}".lower().replace(" ", "_")


class KbScope:
    """
    一次研究运行允许检索的本地知识库范围。

    `entries` 为空时 `failure_reason` 必须非空：调用方要能区分
    "范围内没有匹配材料"和"根本没解析出可检索的范围"。
    """

    __slots__ = ("entries", "failure_reason")

    def __init__(
        self,
        entries: Optional[List[Dict[str, Any]]] = None,
        failure_reason: str = "",
    ):
        self.entries = list(entries or [])
        if not self.entries and not failure_reason:
            raise ValueError(
                "空范围必须给出 failure_reason，否则下游无法区分"
                "『范围内没材料』与『没解析出范围』"
            )
        self.failure_reason = failure_reason

    @property
    def collections(self) -> List[str]:
        return [e["collection"] for e in self.entries]

    def as_state(self) -> List[Dict[str, Any]]:
        """写进 ResearchState 的可序列化形式（要能进检查点）。"""
        return [dict(e) for e in self.entries]

    def __repr__(self) -> str:
        if not self.entries:
            return f"KbScope(empty: {self.failure_reason})"
        return f"KbScope({len(self.entries)} kb: {self.collections})"


def resolve_kb_scope(
    db: Any,
    user_id: Optional[str],
    kb_name: Optional[str] = None,
) -> KbScope:
    """
    从 PostgreSQL 解析本次运行可检索的知识库。

    ⚠️ **授权判断只在这里做。** Scout 不接受调用方直接传集合名——
    那等于让检索层自己决定能看哪些材料，而它没有做这个判断的信息。

    Args:
        db: SQLAlchemy Session
        user_id: 发起研究的用户。缺失即无法判断授权，返回空范围而非放行。
        kb_name: 指定单个知识库；留空表示该用户名下全部知识库。

    Returns:
        KbScope。`entries` 中每项含 collection / kb_id / kb_name /
        document_count，后者供检索层区分"没上传材料"与"索引丢失"。
    """
    if not user_id:
        return KbScope(failure_reason="未提供用户身份，无法判断可检索的知识库范围")
    if db is None:
        return KbScope(failure_reason="数据库会话不可用，无法解析知识库范围")

    try:
        try:
            from models.knowledge import KnowledgeBase
        except ImportError:
            from app.models.knowledge import KnowledgeBase

        q = db.query(KnowledgeBase).filter(KnowledgeBase.user_id == user_id)
        if kb_name:
            q = q.filter(KnowledgeBase.name == kb_name)
        kbs = q.all()
    except Exception as e:
        logger.error(f"[kb_scope] 解析知识库范围失败: {e}", exc_info=True)
        return KbScope(failure_reason=f"知识库范围解析异常（{type(e).__name__}: {e}）")

    if not kbs:
        reason = (f"用户名下不存在知识库 {kb_name!r}" if kb_name
                  else "用户名下没有任何知识库")
        return KbScope(failure_reason=reason)

    entries = [{
        "collection": collection_name_for(kb.id),
        "kb_id": str(kb.id),
        "kb_name": kb.name,
        "document_count": int(kb.document_count or 0),
    } for kb in kbs]

    logger.info(
        f"[kb_scope] 用户 {user_id} 可检索 {len(entries)} 个知识库: "
        f"{[e['kb_name'] for e in entries]}"
    )
    return KbScope(entries)


def classify_missing_collection(entry: Dict[str, Any]) -> Tuple[bool, str]:
    """
    集合在 Milvus 中不存在时，这是故障还是"本来就没材料"？

    判据取自关系库里的 `document_count`：
      - 声称有文档却查不到集合 → **故障**（索引丢失或改名产生了孤儿集合）
      - 本来就没上传过文档     → 不是故障，如实为空

    没有这个区分，一次索引丢失会以"本地材料未发现相关内容"的形式
    进入报告——又一次把故障写成结论。

    Returns: (是否故障, 原因说明)
    """
    if int(entry.get("document_count") or 0) > 0:
        return True, (
            f"知识库「{entry.get('kb_name')}」登记有 {entry.get('document_count')} 份文档，"
            f"但向量集合 {entry.get('collection')} 不存在（索引丢失或知识库改名后未重建）"
        )
    return False, ""
