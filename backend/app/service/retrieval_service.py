# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""
知识库检索服务 - 基于 Milvus

功能：
1. retrieve_content - 从指定集合检索内容
2. retrieve_from_knowledge_base - 从知识库检索内容
"""

from typing import List, Dict, Any, Optional
from service.milvus_service import get_milvus_service
from service.embedding_service import generate_embedding


def retrieve_content(
    indexNames: str,
    question: str,
    top_k: int = 5,
    kb_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    检索相关内容

    Args:
        indexNames: 集合名称（知识库索引）
        question: 查询问题
        top_k: 返回结果数量
        kb_id: 知识库ID（可选过滤）

    Returns:
        检索结果列表
    """
    try:
        # 1. 生成查询向量
        query_vectors = generate_embedding([question])
        if not query_vectors or len(query_vectors) == 0:
            print("生成查询向量失败")
            return []

        query_vector = query_vectors[0]

        # 2. 执行向量搜索
        milvus = get_milvus_service()
        results = milvus.search(
            collection_name=indexNames,
            query_vector=query_vector,
            top_k=top_k,
            kb_id=kb_id,
        )

        # 3. 格式化结果
        extracted_data = []
        for i, result in enumerate(results, start=1):
            message = {
                "id": i,
                "document_id": result.get("doc_id", "N/A"),
                "document_name": result.get("filename", "N/A"),
                "content_with_weight": result.get("content", ""),
                "score": result.get("score", 0),
            }
            extracted_data.append(message)

        return extracted_data

    except Exception as e:
        print(f"检索错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


def retrieve_from_knowledge_base(
    kb_id: str,
    question: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    从指定知识库检索内容。

    ## 参数从 kb_name 改成了 kb_id（BC-53）

    按**名字**定位知识库有两个问题，第二个是安全问题：

    1. `kb.name` 只在单个用户内唯一，也可被改名——按名找到的可能不是
       调用方以为的那个知识库，或者根本找不到
    2. 更要紧的是：一个只接受名字的检索入口，**无法做授权判断**。
       调用方传什么名字就查什么，谁的都能查

    因此本函数现在只接受知识库 UUID，且**调用方有责任先确认该 UUID
    属于当前用户**——正确做法是走 `service/kb_scope.resolve_kb_scope()`，
    它从 PostgreSQL 按登录用户解析可检索范围。

    Args:
        kb_id: 知识库 UUID
        question: 查询问题
        top_k: 返回结果数量

    Returns:
        检索结果列表
    """
    try:
        from service.kb_scope import collection_name_for
    except ImportError:
        from app.service.kb_scope import collection_name_for

    return retrieve_content(collection_name_for(kb_id), question, top_k)
