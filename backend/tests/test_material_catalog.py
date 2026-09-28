"""Compile real SQLAlchemy queries without a database to check catalog boundaries."""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query, Session, declarative_base

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from service.deep_research_v2.material_catalog import load_material_catalog


Base = declarative_base()


class CatalogKnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id = Column(postgresql.UUID(as_uuid=True), primary_key=True)
    user_id = Column(postgresql.UUID(as_uuid=True))


class CatalogDocument(Base):
    __tablename__ = "documents"
    id = Column(postgresql.UUID(as_uuid=True), primary_key=True)
    knowledge_base_id = Column(postgresql.UUID(as_uuid=True))
    user_id = Column(postgresql.UUID(as_uuid=True))
    filename = Column(String)
    status = Column(String)
    chunk_count = Column(Integer)
    created_at = Column(DateTime)


OWNER = UUID("f0da2e98-caf2-4734-afb4-ac08d990b193")
KB = UUID("f2c69e49-a811-4968-9c43-5d8e68d4a611")
OTHER_KB = UUID("3552d8a6-4d66-4d48-9840-8b90ef59b8bb")


def install_offline_query(monkeypatch, rows):
    captures = []

    def all_rows(query):
        compiled = query.statement.compile(dialect=postgresql.dialect())
        captures.append((str(compiled), compiled.params))
        return rows

    # These are SQLAlchemy models, so joins, filters and limit/order clauses are
    # exercised normally. Only terminal execution and provider imports are fake.
    monkeypatch.setattr(Query, "all", all_rows)
    session_factory = Mock(side_effect=lambda: Session(bind=None))
    monkeypatch.setitem(sys.modules, "core.database", SimpleNamespace(SessionLocal=session_factory))
    monkeypatch.setitem(sys.modules, "models.knowledge", SimpleNamespace(
        Document=CatalogDocument, KnowledgeBase=CatalogKnowledgeBase))
    return captures, session_factory


def row(*, index=0, count=4, status="completed"):
    return SimpleNamespace(id=uuid4(), knowledge_base_id=KB, filename=f"材料{index}.pdf",
                           status=status, chunk_count=count, user_id=OWNER,
                           created_at=datetime(2025, 1, 1), file_path="/private/user/upload.pdf",
                           error_message="private provider traceback")


def test_catalog_query_constrains_both_ownership_columns_and_server_resolved_scope(monkeypatch):
    captures, factory = install_offline_query(monkeypatch, [row()])
    scope = [{"kb_id": str(KB), "collection": "owned"}, {"kb_id": str(OTHER_KB), "collection": "also_owned"}]
    result = load_material_catalog(scope, str(OWNER))
    assert factory.call_count == 1 and result["documents"]
    sql, params = captures[0]
    assert "JOIN knowledge_bases ON documents.knowledge_base_id = knowledge_bases.id" in sql
    assert "knowledge_bases.id IN" in sql
    assert "knowledge_bases.user_id =" in sql and "documents.user_id =" in sql
    assert params["id_1"] == [KB, OTHER_KB]
    assert params["user_id_1"] == OWNER and params["user_id_2"] == OWNER
    assert "ORDER BY documents.created_at, documents.id" in sql
    assert "LIMIT" in sql and 41 in params.values()


@pytest.mark.parametrize("scope, owner", [
    ([], str(OWNER)), ([{"kb_id": str(KB)}], None),
    ([{"kb_id": str(KB)}], "invalid-owner"), ([{"kb_id": "invalid-kb"}], str(OWNER)),
])
def test_missing_identity_or_invalid_scope_cannot_open_database(monkeypatch, scope, owner):
    _, factory = install_offline_query(monkeypatch, [])
    with pytest.raises(ValueError):
        load_material_catalog(scope, owner)
    factory.assert_not_called()


def test_catalog_fetches_41_but_returns_only_40_metadata_rows(monkeypatch):
    rows = [row(index=i, count=-3 if i == 0 else None if i == 1 else 4,
                status="failed" if i == 0 else "pending" if i == 1 else "completed") for i in range(41)]
    captures, _ = install_offline_query(monkeypatch, rows)
    result = load_material_catalog([{"kb_id": str(KB)}], str(OWNER))
    assert result["truncated"] is True and len(result["documents"]) == 40
    assert 41 in captures[0][1].values()
    for item in result["documents"]:
        assert set(item) == {"doc_id", "kb_id", "title", "index_status", "chunk_count"}
        assert isinstance(item["doc_id"], str) and item["kb_id"] == str(KB)
    assert result["documents"][0]["chunk_count"] == result["documents"][1]["chunk_count"] == 0
    assert result["documents"][0]["index_status"] == "failed"
    assert result["documents"][1]["index_status"] == "pending"
    assert "/private/" not in str(result) and "traceback" not in str(result)


@pytest.mark.parametrize("count", [0, 1, 40])
def test_successful_query_at_or_under_limit_is_not_truncated(monkeypatch, count):
    install_offline_query(monkeypatch, [row(index=i) for i in range(count)])
    result = load_material_catalog([{"kb_id": str(KB)}], str(OWNER))
    assert result["truncated"] is False and len(result["documents"]) == count
