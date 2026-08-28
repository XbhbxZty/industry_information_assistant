# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""Milvus 向量存储服务"""
import os
from typing import List, Dict, Any, Optional
from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
)


class MilvusService:
    """Milvus 向量存储服务"""

    def __init__(self):
        self.host = os.getenv("MILVUS_HOST", "localhost")
        self.port = int(os.getenv("MILVUS_PORT", "29530"))
        self.vector_dim = 1024  # text-embedding-v4 维度
        self._connect()

    def _connect(self):
        """连接到 Milvus"""
        try:
            connections.connect(
                alias="default",
                host=self.host,
                port=self.port,
            )
            print(f"已连接到 Milvus: {self.host}:{self.port}")
        except Exception as e:
            print(f"连接 Milvus 失败: {e}")
            raise

    def has_collection(self, collection_name: str) -> bool:
        """
        集合是否存在。

        单独暴露出来，是为了让调用方能在检索**之前**区分
        "集合不存在"与"集合存在但没有命中"。`search()` 对这两种情况
        都返回空列表，调用方从返回值上无从分辨——本地知识库检索
        整条链路失效了很久却没人发现，正是因为这个（见 kb_scope.py）。
        """
        try:
            return utility.has_collection(collection_name)
        except Exception as e:
            print(f"检查集合 {collection_name} 是否存在时出错: {e}")
            return False

    def create_collection(self, collection_name: str) -> Collection:
        """
        创建集合（如果不存在）

        Args:
            collection_name: 集合名称

        Returns:
            Collection 对象
        """
        # 检查集合是否存在
        if utility.has_collection(collection_name):
            print(f"集合 {collection_name} 已存在")
            collection = Collection(collection_name)
            collection.load()
            return collection

        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=128),
            FieldSchema(name="filename", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
            FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.vector_dim),
        ]

        schema = CollectionSchema(fields=fields, description=f"Knowledge base: {collection_name}")
        collection = Collection(name=collection_name, schema=schema)

        # 创建索引
        index_params = {
            "metric_type": "COSINE",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        collection.create_index(field_name="vector", index_params=index_params)

        # 加载集合到内存
        collection.load()

        print(f"集合 {collection_name} 创建成功")
        return collection

    def insert_documents(
        self,
        collection_name: str,
        documents: List[Dict[str, Any]],
    ) -> int:
        """
        插入文档

        Args:
            collection_name: 集合名称
            documents: 文档列表，每个文档包含:
                - id: 文档ID
                - doc_id: 原始文档ID
                - kb_id: 知识库ID
                - filename: 文件名
                - content: 文本内容
                - chunk_index: 切片索引
                - vector: 向量

        Returns:
            插入的文档数量
        """
        collection = self.create_collection(collection_name)

        # 准备数据
        ids = [doc["id"] for doc in documents]
        doc_ids = [doc["doc_id"] for doc in documents]
        kb_ids = [doc["kb_id"] for doc in documents]
        filenames = [doc["filename"] for doc in documents]
        contents = [doc["content"][:65535] for doc in documents]  # 截断过长内容
        chunk_indices = [doc["chunk_index"] for doc in documents]
        vectors = [doc["vector"] for doc in documents]

        # 插入数据
        data = [ids, doc_ids, kb_ids, filenames, contents, chunk_indices, vectors]
        collection.insert(data)
        collection.flush()

        print(f"成功插入 {len(documents)} 条文档到 {collection_name}")
        return len(documents)

    def search(
        self,
        collection_name: str,
        query_vector: List[float],
        top_k: int = 5,
        kb_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量搜索

        Args:
            collection_name: 集合名称
            query_vector: 查询向量
            top_k: 返回结果数量
            kb_id: 知识库ID（可选，用于过滤）

        Returns:
            搜索结果列表
        """
        if not utility.has_collection(collection_name):
            print(f"集合 {collection_name} 不存在")
            return []

        collection = Collection(collection_name)
        collection.load()

        # 构建过滤表达式
        expr = f'kb_id == "{kb_id}"' if kb_id else None

        # 搜索参数
        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 10},
        }

        results = collection.search(
            data=[query_vector],
            anns_field="vector",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=["id", "doc_id", "kb_id", "filename", "content", "chunk_index"],
        )

        # 格式化结果
        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "id": hit.entity.get("id"),
                    "doc_id": hit.entity.get("doc_id"),
                    "kb_id": hit.entity.get("kb_id"),
                    "filename": hit.entity.get("filename"),
                    "content": hit.entity.get("content"),
                    "chunk_index": hit.entity.get("chunk_index"),
                    "score": hit.score,
                })

        return formatted_results

    def delete_by_doc_id(self, collection_name: str, doc_id: str) -> bool:
        """
        根据文档ID删除所有相关切片

        Args:
            collection_name: 集合名称
            doc_id: 文档ID

        Returns:
            是否成功
        """
        if not utility.has_collection(collection_name):
            return True

        try:
            collection = Collection(collection_name)
            expr = f'doc_id == "{doc_id}"'
            collection.delete(expr)
            print(f"已删除文档 {doc_id} 的所有切片")
            return True
        except Exception as e:
            print(f"删除文档失败: {e}")
            return False

    def delete_collection(self, collection_name: str) -> bool:
        """
        删除集合

        Args:
            collection_name: 集合名称

        Returns:
            是否成功
        """
        try:
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)
                print(f"集合 {collection_name} 已删除")
            return True
        except Exception as e:
            print(f"删除集合失败: {e}")
            return False

    def get_collection_stats(self, collection_name: str) -> Dict[str, Any]:
        """
        获取集合统计信息

        Args:
            collection_name: 集合名称

        Returns:
            统计信息
        """
        if not utility.has_collection(collection_name):
            return {"exists": False}

        collection = Collection(collection_name)
        return {
            "exists": True,
            "name": collection_name,
            "num_entities": collection.num_entities,
        }

    def iter_all_rows(
        self,
        collection_name: str,
        batch_size: int = 1000,
        include_vector: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        读出集合全部行（可含向量），供迁移搬运使用。

        ⚠️ 只给迁移脚本用，不要在请求路径上调用。

        带上向量是关键：搬运时无需重新调用 embedding 接口，
        既省钱又保证迁移前后的向量**逐位相同**——重新向量化会引入
        模型版本差异，让"迁移前后检索结果应当一致"这条断言不再成立。

        Milvus 的 `query` 需要非空 expr，VARCHAR 主键用 `id != ""` 全匹配。
        """
        if not utility.has_collection(collection_name):
            return []

        fields = ["id", "doc_id", "kb_id", "filename", "content", "chunk_index"]
        if include_vector:
            fields.append("vector")

        collection = Collection(collection_name)
        collection.load()

        rows: List[Dict[str, Any]] = []
        offset = 0
        while True:
            batch = collection.query(
                expr='id != ""',
                output_fields=fields,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < batch_size:
                break
            offset += batch_size
        return rows

    def list_collections(self) -> List[str]:
        """列出全部集合名。供迁移脚本盘点现状。"""
        try:
            return list(utility.list_collections())
        except Exception as e:
            print(f"列出集合失败: {e}")
            return []

    def get_document_chunks(
        self,
        collection_name: str,
        doc_id: str,
        limit: int = 4000,
    ) -> List[Dict[str, Any]]:
        """按 doc_id 取一份文档的全部切片（供报表口径判定）。

        口径分节标题（`七、合并财务报表项目注释` / `十八、母公司财务报表主要
        项目注释`）与被引用的数据行几乎从不在同一切片里——实测两者相隔
        50 多个切片。只看被引切片永远判不出口径，必须能回溯同文档的前序内容。
        """
        if not utility.has_collection(collection_name):
            print(f"集合 {collection_name} 不存在")
            return []
        try:
            collection = Collection(collection_name)
            collection.load()
            rows = collection.query(
                expr=f'doc_id == "{doc_id}"',
                output_fields=["doc_id", "content", "chunk_index"],
                limit=limit,
            )
            rows.sort(key=lambda row: row.get("chunk_index", 0))
            return rows
        except Exception as e:
            print(f"按 doc_id 查询切片失败: {e}")
            return []

    def get_chunks_by_filename(
        self,
        collection_name: str,
        filename: str,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """
        根据文件名获取所有切片

        Args:
            collection_name: 集合名称
            filename: 文件名
            limit: 最大返回数量

        Returns:
            切片列表
        """
        if not utility.has_collection(collection_name):
            print(f"集合 {collection_name} 不存在")
            return []

        try:
            collection = Collection(collection_name)
            collection.load()

            # 查询表达式
            expr = f'filename == "{filename}"'

            results = collection.query(
                expr=expr,
                output_fields=["id", "doc_id", "kb_id", "filename", "content", "chunk_index"],
                limit=limit,
            )

            # 按 chunk_index 排序
            results.sort(key=lambda x: x.get("chunk_index", 0))

            return results
        except Exception as e:
            print(f"查询切片失败: {e}")
            return []


# 单例实例
_milvus_service: Optional[MilvusService] = None


def get_milvus_service() -> MilvusService:
    """获取 Milvus 服务单例"""
    global _milvus_service
    if _milvus_service is None:
        _milvus_service = MilvusService()
    return _milvus_service
