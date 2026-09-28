"""Read-only material names from the relational ownership boundary.

The caller supplies server-resolved scope, never model-selected KB IDs. This
catalogue is not evidence: documents still have to be read from their index.
"""
from uuid import UUID


def load_material_catalog(scope, user_id):
    if not user_id or not scope:
        raise ValueError("没有用户身份或授权资料范围，不能枚举材料")
    owner = UUID(str(user_id))
    kb_ids = [UUID(str(entry["kb_id"])) for entry in scope]
    try:
        from core.database import SessionLocal
        from models.knowledge import Document, KnowledgeBase
    except ImportError:
        from app.core.database import SessionLocal
        from app.models.knowledge import Document, KnowledgeBase
    with SessionLocal() as db:
        documents = (
            db.query(Document)
            .join(KnowledgeBase, Document.knowledge_base_id == KnowledgeBase.id)
            .filter(KnowledgeBase.id.in_(kb_ids), KnowledgeBase.user_id == owner,
                    Document.user_id == owner)
            .order_by(Document.created_at, Document.id)
            .limit(41).all()
        )
        return {"documents": [
            {"doc_id": str(doc.id), "kb_id": str(doc.knowledge_base_id),
             "title": doc.filename, "index_status": doc.status,
             "chunk_count": max(0, int(doc.chunk_count or 0))}
            for doc in documents[:40]
        ], "truncated": len(documents) > 40}
