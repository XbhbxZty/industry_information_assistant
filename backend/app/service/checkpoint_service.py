# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""检查点服务 - 用于保存和恢复深度研究状态"""
import copy
import json
import logging
from typing import Dict, Any, Optional, List
from uuid import UUID
from datetime import datetime
from sqlalchemy.orm import Session

from models.research import ResearchCheckpoint, ResearchCheckpointIntegrity
from core.database import SessionLocal
from service.checkpoint_integrity import (
    CheckpointIntegrityError,
    INTEGRITY_VERSION,
    MODE_MANAGED,
    MODE_STANDARD,
    business_key_id,
    issue_business_state_seal,
    mode_from_state,
    verify_business_state_seal,
    verify_graph_state_seal,
)

logger = logging.getLogger(__name__)


class CheckpointService:
    """检查点服务"""

    def __init__(self):
        pass

    def _get_db(self) -> Session:
        """获取数据库会话"""
        return SessionLocal()

    @staticmethod
    def _checkpoint_id(checkpoint: ResearchCheckpoint) -> str:
        checkpoint_id = getattr(checkpoint, "id", None)
        if checkpoint_id is None:
            raise CheckpointIntegrityError("检查点缺少持久化标识")
        return str(checkpoint_id)

    @staticmethod
    def _integrity_row(db: Session, session_id: str) -> Optional[ResearchCheckpointIntegrity]:
        return db.query(ResearchCheckpointIntegrity).filter(
            ResearchCheckpointIntegrity.session_id == session_id
        ).first()

    def _verify_integrity_pair(
        self,
        checkpoint: ResearchCheckpoint,
        integrity: ResearchCheckpointIntegrity,
        session_id: str,
        required_mode: Optional[str] = None,
    ) -> str:
        """Verify persisted metadata and both seals before accepting an update/read."""
        if integrity.session_id != session_id:
            raise CheckpointIntegrityError("检查点完整性 session_id 不一致")
        if integrity.checkpoint_id != self._checkpoint_id(checkpoint):
            raise CheckpointIntegrityError("检查点完整性 checkpoint_id 不一致")

        mode = integrity.mode
        if mode not in (MODE_MANAGED, MODE_STANDARD):
            raise CheckpointIntegrityError("检查点完整性模式非法")
        if required_mode is not None and mode != required_mode:
            raise CheckpointIntegrityError("检查点完整性模式不可漂移")
        if integrity.integrity_version != INTEGRITY_VERSION:
            raise CheckpointIntegrityError("检查点完整性版本不受支持")
        if integrity.key_id != business_key_id(mode):
            raise CheckpointIntegrityError("检查点完整性密钥标识不一致")

        revision = integrity.business_revision
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise CheckpointIntegrityError("检查点完整性业务版本非法")

        state = checkpoint.state_json
        verify_graph_state_seal(state, mode)
        verify_business_state_seal(
            state,
            session_id,
            mode,
            revision,
            integrity.business_seal,
        )
        return mode

    def save_checkpoint(
        self,
        session_id: str,
        state: Dict[str, Any],
        user_id: Optional[str] = None,
        ui_state: Optional[Dict[str, Any]] = None,
        final_report: Optional[str] = None,
    ) -> Optional[str]:
        """
        保存检查点

        Args:
            session_id: 研究会话 ID
            state: ResearchState 字典（后端状态）
            user_id: 用户 ID（可选）
            ui_state: 前端 UI 状态（研究步骤、搜索结果、图表等）
            final_report: 最终报告内容

        Returns:
            检查点 ID，失败返回 None
        """
        db = self._get_db()
        try:
            mode = mode_from_state(state)
            if mode not in (MODE_MANAGED, MODE_STANDARD):
                raise CheckpointIntegrityError("检查点缺少完整性模式或图封签")
            # The graph seal authenticates the uncleaned graph state.  It is
            # checked before storage cleaning deliberately, so the cleaner can
            # never normalize an attacker-controlled value into a valid state.
            verify_graph_state_seal(state, mode)

            # 提取关键信息
            query = state.get("query", "")
            phase = state.get("phase", "planning")
            iteration = state.get("iteration", 0)

            # 清理 state 中不可序列化的内容
            clean_state = self._clean_state_for_storage(state)
            clean_ui_state = self._clean_state_for_storage(ui_state) if ui_state else None
            # The JSONB payload is the value that will later be restored and
            # verified.  Reject any cleaner-induced semantic change now rather
            # than committing a checkpoint whose graph seal can never validate
            # on the next read.
            verify_graph_state_seal(clean_state, mode)

            # 查找现有检查点
            existing = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).first()
            integrity = self._integrity_row(db, session_id)

            if integrity and not existing:
                raise CheckpointIntegrityError("检查点完整性记录没有对应检查点")

            if existing and integrity:
                # Do not overwrite a record whose previously persisted state
                # has already failed validation.  This preserves fail-closed
                # semantics across writes, not just restores.
                self._verify_integrity_pair(existing, integrity, session_id, mode)
                business_revision = integrity.business_revision + 1
            else:
                business_revision = 1

            if existing:
                # 更新现有检查点
                existing.phase = phase
                existing.iteration = iteration
                existing.state_json = clean_state
                if clean_ui_state:
                    existing.ui_state_json = clean_ui_state
                if final_report:
                    existing.final_report = final_report
                existing.status = "running"
                existing.updated_at = datetime.utcnow()
                checkpoint_id = str(existing.id)
            else:
                # 创建新检查点
                checkpoint = ResearchCheckpoint(
                    session_id=session_id,
                    user_id=UUID(user_id) if user_id else None,
                    query=query,
                    phase=phase,
                    iteration=iteration,
                    state_json=clean_state,
                    ui_state_json=clean_ui_state,
                    final_report=final_report,
                    status="running",
                )
                db.add(checkpoint)
                db.flush()
                existing = checkpoint
                checkpoint_id = self._checkpoint_id(checkpoint)

            # This MAC covers the state actually sent to JSONB, rather than
            # the pre-cleaning graph object.  The graph seal remains separately
            # verifiable inside that cleaned state.
            business_seal = issue_business_state_seal(
                clean_state, session_id, mode, business_revision
            )
            if integrity:
                # checkpoint_id and mode are intentionally immutable after the
                # first write.  _verify_integrity_pair above proved they match.
                integrity.key_id = business_key_id(mode)
                integrity.business_revision = business_revision
                integrity.business_seal = business_seal
                integrity.updated_at = datetime.utcnow()
            else:
                db.add(ResearchCheckpointIntegrity(
                    session_id=session_id,
                    checkpoint_id=checkpoint_id,
                    mode=mode,
                    integrity_version=INTEGRITY_VERSION,
                    key_id=business_key_id(mode),
                    business_revision=business_revision,
                    business_seal=business_seal,
                ))

            db.commit()
            # 详细日志
            ui_steps = clean_ui_state.get("research_steps", []) if clean_ui_state else []
            ui_search = clean_ui_state.get("search_results", []) if clean_ui_state else []
            ui_charts = clean_ui_state.get("charts", []) if clean_ui_state else []
            ui_kg = clean_ui_state.get("knowledge_graph", {}) if clean_ui_state else {}
            logger.info(f"[CheckpointService] 保存成功: session={session_id}, phase={phase}, "
                       f"ui_state=[steps={len(ui_steps)}, search_results={len(ui_search)}, "
                       f"charts={len(ui_charts)}, kg_nodes={len(ui_kg.get('nodes', []) if ui_kg else [])}]")
            return checkpoint_id

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    def load_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        加载最新的检查点（仅后端状态）

        Args:
            session_id: 研究会话 ID

        Returns:
            ResearchState 字典，未找到返回 None
        """
        db = self._get_db()
        try:
            checkpoint = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).order_by(ResearchCheckpoint.updated_at.desc()).first()

            if not checkpoint:
                return None

            integrity = self._integrity_row(db, session_id)
            if not integrity:
                # Legacy rows predate the paired metadata table and retain the
                # old restore behavior by design.
                return checkpoint.state_json

            mode = self._verify_integrity_pair(checkpoint, integrity, session_id)
            result = copy.deepcopy(checkpoint.state_json)
            result["_checkpoint_integrity_mode"] = mode
            return result

        except CheckpointIntegrityError:
            raise
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return None
        finally:
            db.close()

    def load_full_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        加载完整的检查点（包含后端状态、UI状态和报告）

        Args:
            session_id: 研究会话 ID

        Returns:
            完整检查点数据，包含 state_json, ui_state_json, final_report 等
        """
        db = self._get_db()
        try:
            checkpoint = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).order_by(ResearchCheckpoint.updated_at.desc()).first()

            if not checkpoint:
                logger.info(f"[CheckpointService] 未找到检查点: session={session_id}")
                return None

            integrity = self._integrity_row(db, session_id)
            if integrity:
                # Verify but do not add the private restore-only mode field to
                # state_json: this method feeds the API response.
                self._verify_integrity_pair(checkpoint, integrity, session_id)

            result = checkpoint.to_dict(include_state=True)
            # 详细日志
            ui_state = result.get("ui_state_json", {})
            if ui_state:
                logger.info(f"[CheckpointService] 加载成功: session={session_id}, phase={result.get('phase')}, "
                           f"ui_state=[steps={len(ui_state.get('research_steps', []))}, "
                           f"search_results={len(ui_state.get('search_results', []))}, "
                           f"charts={len(ui_state.get('charts', []))}, "
                           f"kg_nodes={len((ui_state.get('knowledge_graph') or {}).get('nodes', []))}]")
            else:
                logger.info(f"[CheckpointService] 加载成功但无ui_state: session={session_id}, phase={result.get('phase')}")
            return result

        except CheckpointIntegrityError:
            raise
        except Exception as e:
            logger.error(f"Failed to load full checkpoint: {e}")
            return None
        finally:
            db.close()

    def get_checkpoint_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取检查点信息（不包含完整状态）

        Args:
            session_id: 研究会话 ID

        Returns:
            检查点元信息
        """
        db = self._get_db()
        try:
            checkpoint = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).order_by(ResearchCheckpoint.updated_at.desc()).first()

            if not checkpoint:
                return None

            return checkpoint.to_dict()

        except Exception as e:
            logger.error(f"Failed to get checkpoint info: {e}")
            return None
        finally:
            db.close()

    def list_checkpoints(
        self,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        列出检查点

        Args:
            user_id: 用户 ID（可选，用于过滤）
            status: 状态过滤
            limit: 限制数量

        Returns:
            检查点列表
        """
        db = self._get_db()
        try:
            query = db.query(ResearchCheckpoint)

            if user_id:
                query = query.filter(ResearchCheckpoint.user_id == UUID(user_id))
            if status:
                query = query.filter(ResearchCheckpoint.status == status)

            checkpoints = query.order_by(
                ResearchCheckpoint.updated_at.desc()
            ).limit(limit).all()

            return [cp.to_dict() for cp in checkpoints]

        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []
        finally:
            db.close()

    def update_status(
        self,
        session_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        更新检查点状态

        Args:
            session_id: 研究会话 ID
            status: 新状态 (running/paused/completed/failed)
            error_message: 错误信息（可选）

        Returns:
            是否成功
        """
        db = self._get_db()
        try:
            checkpoint = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).first()

            if not checkpoint:
                return False

            checkpoint.status = status
            if error_message:
                checkpoint.error_message = error_message
            checkpoint.updated_at = datetime.utcnow()

            db.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to update checkpoint status: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def delete_checkpoint(self, session_id: str) -> bool:
        """
        删除检查点

        Args:
            session_id: 研究会话 ID

        Returns:
            是否成功
        """
        db = self._get_db()
        try:
            # Kept in the same transaction as the checkpoint deletion.  There
            # is deliberately no FK so old installations need no ALTER TABLE.
            db.query(ResearchCheckpointIntegrity).filter(
                ResearchCheckpointIntegrity.session_id == session_id
            ).delete()
            deleted = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).delete()

            db.commit()
            return deleted > 0

        except Exception as e:
            logger.error(f"Failed to delete checkpoint: {e}")
            db.rollback()
            return False
        finally:
            db.close()

    def get_checkpoint_integrity_mode(self, session_id: str) -> Optional[str]:
        """Return a verified checkpoint integrity mode, if the row is modern.

        A missing integrity row is the explicit legacy case.  Any present row
        is a security boundary and is therefore fully verified rather than
        silently treated as legacy when malformed or tampered.
        """
        db = self._get_db()
        try:
            integrity = self._integrity_row(db, session_id)
            if not integrity:
                return None
            checkpoint = db.query(ResearchCheckpoint).filter(
                ResearchCheckpoint.session_id == session_id
            ).order_by(ResearchCheckpoint.updated_at.desc()).first()
            if not checkpoint:
                raise CheckpointIntegrityError("检查点完整性记录没有对应检查点")
            return self._verify_integrity_pair(checkpoint, integrity, session_id)
        except CheckpointIntegrityError:
            raise
        except Exception as e:
            logger.error(f"Failed to get checkpoint integrity mode: {e}")
            return None
        finally:
            db.close()

    def _clean_state_for_storage(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        清理状态以便存储，移除不可序列化的内容。

        注意两个易错点（见 BADCASES.md BC-09）：

        1. 检测时**不能**用 `json.dumps(value, default=str)`。default=str 会把任意
           对象兜底转成字符串，导致检测永远不抛异常，原始对象被当作"可序列化"
           原样存入，最终在 SQLAlchemy 写 JSONB 时才炸。
        2. 运行时会往 state 注入以下划线开头的私有字段（`_message_queue: asyncio.Queue`、
           `_user_id`），它们不属于可持久化状态，直接跳过。
        """
        clean = {}
        for key, value in state.items():
            # 运行时注入的私有字段不持久化
            if isinstance(key, str) and key.startswith("_"):
                continue
            try:
                # 严格检测：不加 default，真正不可序列化的值才会抛异常
                json.dumps(value)
                clean[key] = value
            except (TypeError, ValueError):
                if isinstance(value, dict):
                    clean[key] = self._clean_state_for_storage(value)
                elif isinstance(value, (list, tuple)):
                    clean[key] = [
                        self._clean_state_for_storage(v) if isinstance(v, dict) else str(v)
                        for v in value
                    ]
                else:
                    clean[key] = str(value)
        return clean


# 单例
_checkpoint_service = None


def get_checkpoint_service() -> CheckpointService:
    """获取检查点服务实例"""
    global _checkpoint_service
    if _checkpoint_service is None:
        _checkpoint_service = CheckpointService()
    return _checkpoint_service
