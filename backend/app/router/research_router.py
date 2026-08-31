# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
from typing import Dict, Any, Optional, Literal
import json
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.status import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
import logging

from sqlalchemy.orm import Session

from service import ResearchService, ServiceConfig
from service.dr_g import serialize_event  # 导入序列化函数
from service.kb_scope import resolve_kb_scope
from core.database import get_db
from core.redis_client import cache  # 导入 Redis 缓存
from models.user import User
from router.auth_router import get_current_user_required, require_human_reviewer
from schemas.review_workspace import ReviewTaskListResponse, ReviewTaskPacket
from service.review_workspace_service import (
    ReviewTaskNotFound,
    ReviewTaskNotPending,
    ReviewWorkspaceIntegrityError,
    ReviewWorkspaceService,
    ReviewWorkspaceUnavailable,
)

# V2 导入
from service.deep_research_v2.service import DeepResearchV2Service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ResearchRouter")

# 取消标志 key 前缀
CANCEL_KEY_PREFIX = "research:cancel:"

# 创建路由实例
router = APIRouter(prefix="/research", tags=["research"])

# 请求模型
class ResearchRequest(BaseModel):
    """深度研究请求模型"""
    query: str
    session_id: Optional[str] = None  # 会话 ID（用于检查点保存）
    max_iterations: Optional[int] = 3
    kb_name: Optional[str] = None  # 本地知识库名称
    search_web: Optional[bool] = None  # 是否搜索网络 (兼容旧版)
    search_local: Optional[bool] = None  # 是否搜索本地知识库 (兼容旧版)
    search_modes: Optional[list] = None  # 搜索模式: ['web', 'local'] (新版)
    version: Optional[Literal["v1", "v2"]] = "v2"  # 版本选择 (v2: 多智能体架构，推荐)
    # 研究截止日（ISO 日期，如 "2025-05-31"）。留空 = 不设时点闸门。
    # 设定后，晚于该日发布/发生的事实不得进入判断——用于回溯评测与报告可复现。
    # 见 service/verification.py::check_as_of
    as_of: Optional[str] = None
    # 尽调入口不再依赖 companies.json 命中。前端可显式声明主体与业务场景；
    # 老客户端不传时，服务层仍会按查询中的“尽调/授信/保理”等语义兜底。
    subject_name: Optional[str] = None
    business_type: Optional[str] = None
    due_diligence: Optional[bool] = None
    # 只接受受控档案 ID；档案内容与修订号必须在服务端按登录身份和 DB 会话读取，
    # 不接受客户端提交的 payload/ref，以免把任意数据伪装成已发布快照。
    company_profile_id: Optional[str] = None
    # 调查层（B 层）开关。留空 = 随尽调模式默认开启（计划 9.1）。
    # 它只控制**探索性**抽取；确定性图表来自已核实字段，不受此开关影响，
    # 也不该受——那些图与证据附录同源，关掉它们等于让报告少说已核实的事。
    investigation: Optional[bool] = None

    class Config:
        json_schema_extra = {
            "example": {
                "query": "中国安责险的市场现状和未来发展趋势是什么？请提供具体数据支持。",
                "session_id": None,
                "max_iterations": 3,
                "kb_name": None,
                "search_modes": ["web", "local"],
                "version": "v2"
            }
        }

    def get_search_web(self) -> bool:
        """获取是否搜索网络"""
        if self.search_modes is not None:
            return 'web' in self.search_modes
        return self.search_web if self.search_web is not None else True

    def get_search_local(self) -> bool:
        """获取是否搜索本地知识库"""
        if self.search_modes is not None:
            return 'local' in self.search_modes
        return self.search_local if self.search_local is not None else False


class HumanReviewRequest(BaseModel):
    """
    风控复核结论（v0.6 人机协同）

    复核人身份不接受客户端传入，而是由服务端从登录 Token 中取得。
    这样既能留痕，也不能通过篡改请求体冒充其他复核人。
    """
    approved: bool                                  # 是否通过
    comment: Optional[str] = ""                     # 复核意见
    override_level: Optional[str] = None            # 人工调整后的风险等级

    class Config:
        extra = "forbid"
        json_schema_extra = {
            "example": {
                "approved": True,
                "comment": "已复核司法数据源缺口，要求追加担保后可授信",
                "override_level": None,
            }
        }


# 获取服务实例
def get_research_service():
    """获取研究服务实例"""
    config = ServiceConfig.get_api_config()
    research_service = ResearchService(
        search_api_key=config.get('bochaai_api_key'),
        llm_api_key=config.get('dashscope_api_key'),
        llm_base_url=config.get('dashscope_base_url')
    )
    return {"research_service": research_service}


def get_research_service_v2():
    """获取 V2 研究服务实例（使用配置文件中的模型设置）"""
    # 直接创建服务，配置从 llm_config.py 读取
    return DeepResearchV2Service()


def _resolve_scope(
    db: Session,
    current_user: User,
    kb_name: Optional[str],
    search_local: bool,
) -> list:
    """
    解析本次研究可检索的本地知识库。

    未启用本地检索时返回空列表——不去查库，也不会因此产生检索故障
    （Scout 只在 `search_local=True` 时走本地路径）。

    ⚠️ 范围永远按**登录用户**解析，不接受客户端传集合名。
    超级用户在这里也不放宽：检查点复核需要跨用户可见性，
    而把别人上传的财报召回进自己的尽调报告是另一回事。
    """
    if not search_local:
        return []
    scope = resolve_kb_scope(db, str(current_user.id), kb_name)
    if not scope.entries:
        # 不在这里抛错：启用了本地检索却没有知识库是常见情形，
        # 应当由 Scout 记为检索故障并写进报告，而不是让整个研究请求失败。
        logger.warning(f"[research] 本地检索范围为空：{scope.failure_reason}")
    return scope.as_state()


def _assert_checkpoint_access(info: Dict[str, Any], current_user: User) -> None:
    """检查点仅允许归属用户访问；超级用户承担跨用户复核/运维职责。"""
    owner_id = info.get("user_id")
    if current_user.is_superuser:
        return
    if not owner_id or str(owner_id) != str(current_user.id):
        # 对历史遗留的无归属检查点同样失败关闭，不能把旧数据变成公共数据。
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="无权访问该研究会话")


def _assert_review_target(info: Dict[str, Any], current_user: User) -> str:
    """Require an owned checkpoint and separation between owner and reviewer.

    A reviewer capability grants no generic checkpoint access.  It only permits
    a non-owner to submit a decision for an existing, owned review target.
    Legacy checkpoints without an owner cannot prove this separation, so they
    are deliberately rejected rather than becoming reviewable by everyone.
    """
    owner_id = info.get("user_id")
    if not owner_id:
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="无归属研究会话不能提交人工复核",
        )
    if str(owner_id) == str(current_user.id):
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="研究发起人不得审核自己的会话",
        )
    return str(owner_id)


def _raise_review_workspace_error(exc: Exception) -> None:
    """Map strict review reader errors without concealing damaged state."""
    if isinstance(exc, ReviewTaskNotFound):
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ReviewTaskNotPending):
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ReviewWorkspaceIntegrityError):
        raise HTTPException(status_code=HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, ReviewWorkspaceUnavailable):
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="复核工作台暂不可用",
        ) from exc
    raise exc


def _load_admin_company_profile_snapshot(db: Session, company_profile_id: str) -> Dict[str, Any]:
    """Read one active admin profile before streaming and freeze it as JSON.

    The company-profile CRUD service is the only persistence boundary.  This
    router deliberately does not accept a profile payload and never falls back
    to ``companies.json`` when an explicit ID cannot be resolved.
    """
    requested_id = str(company_profile_id or "").strip()
    if not requested_id:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="company_profile_id 不能为空")

    try:
        try:
            from service.admin_company_profile_service import (
                AdminCompanyProfileIntegrityError,
                AdminCompanyProfileNotFound,
                AdminCompanyProfileValidationError,
                get_active_profile_snapshot,
            )
        except ImportError:
            from app.service.admin_company_profile_service import (  # type: ignore
                AdminCompanyProfileIntegrityError,
                AdminCompanyProfileNotFound,
                AdminCompanyProfileValidationError,
                get_active_profile_snapshot,
            )
    except ImportError as exc:
        # Do not silently degrade an explicit managed-profile request to fuzzy
        # name matching while the CRUD feature is unavailable.
        logger.error("[research] 管理端企业档案服务不可用", exc_info=True)
        raise HTTPException(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            detail="企业档案服务不可用，无法加载指定快照",
        ) from exc

    try:
        snapshot = get_active_profile_snapshot(db, requested_id)
    except AdminCompanyProfileNotFound as exc:
        raise HTTPException(status_code=404, detail="企业档案不存在或已归档") from exc
    except AdminCompanyProfileIntegrityError as exc:
        raise HTTPException(
            status_code=HTTP_409_CONFLICT,
            detail=f"企业档案审计连续性校验失败：{exc}",
        ) from exc
    except AdminCompanyProfileValidationError as exc:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail=f"企业档案快照无效：{exc}") from exc

    # ``json`` round-trip is intentional.  It both rejects ORM/Pydantic objects
    # leaking across the streaming boundary and breaks all references to the
    # active database object before the async generator starts.
    try:
        frozen = json.loads(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="企业档案服务返回的快照不是纯 JSON",
        ) from exc

    if not isinstance(frozen, dict) or set(frozen) != {"profile", "ref", "scenario"}:
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="企业档案快照形状非法")
    if not isinstance(frozen["profile"], dict) or not isinstance(frozen["ref"], dict):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="企业档案快照内容非法")
    if not isinstance(frozen["scenario"], str):
        raise HTTPException(status_code=HTTP_400_BAD_REQUEST, detail="企业档案快照场景非法")
    return frozen

@router.post("/stream", status_code=HTTP_200_OK)
async def stream_research(
    request: ResearchRequest,
    services: Dict[str, Any] = Depends(get_research_service),
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """
    深度研究接口 - 流式输出

    对用户的研究问题执行全面的深度研究，包括问题分解、网络搜索、信息整合、数据分析和报告生成。
    使用 Server-Sent Events (SSE) 格式流式返回整个研究过程和结果。

    支持两个版本：
    - v1: 传统 ReAct 架构
    - v2: 多智能体协作网络（推荐）

    Args:
        request: 包含研究问题和配置的请求体

    Returns:
        流式响应，包含研究过程和结果的 SSE 格式数据
    """
    # 根据版本选择服务
    if request.version == "v2":
        if request.session_id:
            from service.checkpoint_service import get_checkpoint_service
            existing = get_checkpoint_service().get_checkpoint_info(request.session_id)
            if existing:
                _assert_checkpoint_access(existing, current_user)
                raise HTTPException(
                    status_code=HTTP_400_BAD_REQUEST,
                    detail="该 session_id 已存在，请使用恢复接口而不是覆盖检查点",
                )
        search_web = request.get_search_web()
        search_local = request.get_search_local()
        logger.info(f"Using DeepResearch V2 for query: {request.query[:50]}... (session_id: {request.session_id}, search_web={search_web}, search_local={search_local})")
        service_v2 = get_research_service_v2()
        admin_snapshot = None
        if request.company_profile_id is not None:
            # 必须在返回 StreamingResponse 前完成：流开始后再查库既会失去
            # 事务/登录边界，也可能把执行期间的新版本混进同一份研究状态。
            admin_snapshot = _load_admin_company_profile_snapshot(db, request.company_profile_id)
        # 本地检索范围在**这一层**解析：授权信息在 PostgreSQL 的
        # KnowledgeBase.user_id 里，Milvus schema 中没有 user_id。
        # 检索层拿不到做这个判断的信息，就不该由它拼装集合名。
        kb_scope = _resolve_scope(db, current_user, request.kb_name, search_local)

        async def generate_sse_v2():
            try:
                async for event in service_v2.research(
                    query=request.query,
                    session_id=request.session_id,
                    kb_name=request.kb_name,
                    search_web=search_web,
                    search_local=search_local,
                    max_iterations=request.max_iterations,
                    user_id=str(current_user.id),
                    as_of=request.as_of or "",
                    kb_scope=kb_scope,
                    subject_name=request.subject_name or "",
                    business_type=request.business_type or "",
                    due_diligence=request.due_diligence,
                    investigation=request.investigation,
                    provided_company_profile=(admin_snapshot or {}).get("profile"),
                    admin_profile_ref=(admin_snapshot or {}).get("ref"),
                    admin_profile_scenario=(admin_snapshot or {}).get("scenario", ""),
                ):
                    yield event
            except Exception as e:
                logger.error(f"V2 Research error: {e}")
                error_event = serialize_event({"type": "error", "content": str(e)})
                yield f"data: {error_event}\n\n"

        return StreamingResponse(
            generate_sse_v2(),
            media_type="text/event-stream"
        )

    if request.company_profile_id is not None:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail="company_profile_id 仅支持 v2 研究流程",
        )

    # V1 原有逻辑
    research_service = services["research_service"]

    async def generate_sse():
        try:
            async for event in research_service.research_stream(
                query=request.query,
                max_iterations=request.max_iterations,
                kb_name=request.kb_name,
                search_web=request.search_web,
                search_local=request.search_local
            ):
                # 将事件转换为 SSE 格式
                yield f"data: {event}\n\n"
        except Exception as e:
            # 使用serialize_event进行错误处理，确保JSON格式正确
            error_event = serialize_event({"type": "error", "content": str(e)})
            yield f"data: {error_event}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream"
    )

@router.get("/stream", status_code=HTTP_200_OK)
async def stream_research_get(
    query: str = Query(..., description="研究问题", example="中国安责险的市场现状和未来发展趋势是什么？"),
    max_iterations: int = Query(3, description="最大迭代次数", ge=1, le=5),
    kb_name: Optional[str] = Query(None, description="本地知识库名称"),
    search_web: bool = Query(True, description="是否搜索网络"),
    search_local: bool = Query(True, description="是否搜索本地知识库"),
    as_of: str = Query("", description="研究截止日（ISO 日期）。留空=不设时点闸门"),
    version: str = Query("v1", description="版本: v1 或 v2"),
    services: Dict[str, Any] = Depends(get_research_service),
    current_user: User = Depends(get_current_user_required),
    db: Session = Depends(get_db),
):
    """
    深度研究接口 - GET方式流式输出

    对用户的研究问题执行全面的深度研究，包括问题分解、网络搜索、信息整合、数据分析和报告生成。
    使用 Server-Sent Events (SSE) 格式流式返回整个研究过程和结果。

    支持两个版本：
    - v1: 传统 ReAct 架构
    - v2: 多智能体协作网络（推荐）

    Args:
        query: 研究问题
        max_iterations: 最大迭代次数（范围：1-5）
        version: 版本选择 (v1 或 v2)

    Returns:
        流式响应，包含研究过程和结果的 SSE 格式数据
    """
    # 根据版本选择服务
    if version == "v2":
        logger.info(f"Using DeepResearch V2 (GET) for query: {query[:50]}...")
        service_v2 = get_research_service_v2()
        kb_scope = _resolve_scope(db, current_user, kb_name, search_local)

        async def generate_sse_v2():
            try:
                # 注意：search_web / search_local 必须显式转发。
                # 曾因漏传导致 GET 端点的这些查询参数被静默忽略（见 BADCASES.md BC-05）
                async for event in service_v2.research(
                    query=query,
                    kb_name=kb_name,
                    search_web=search_web,
                    search_local=search_local,
                    max_iterations=max_iterations,
                    user_id=str(current_user.id),
                    as_of=as_of,
                    kb_scope=kb_scope,
                ):
                    yield event
            except Exception as e:
                logger.error(f"V2 Research error: {e}")
                error_event = serialize_event({"type": "error", "content": str(e)})
                yield f"data: {error_event}\n\n"

        return StreamingResponse(
            generate_sse_v2(),
            media_type="text/event-stream"
        )

    # V1 原有逻辑
    research_service = services["research_service"]

    async def generate_sse():
        try:
            async for event in research_service.research_stream(
                query=query,
                max_iterations=max_iterations,
                kb_name=kb_name,
                search_web=search_web,
                search_local=search_local
            ):
                # 将事件转换为 SSE 格式
                yield f"data: {event}\n\n"
        except Exception as e:
            # 使用serialize_event进行错误处理，确保JSON格式正确
            error_event = serialize_event({"type": "error", "content": str(e)})
            yield f"data: {error_event}\n\n"

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream"
    )


@router.get("/test-wizard", status_code=HTTP_200_OK)
async def test_wizard_endpoint(
    current_user: User = Depends(get_current_user_required),
):
    """
    测试 CodeWizard 数据分析功能（绕过搜索阶段）

    使用模拟数据直接测试图表生成功能。
    """
    if not current_user.is_superuser:
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅超级用户可运行 Wizard 测试")

    from service.deep_research_v2.agents.wizard import CodeWizard
    from service.deep_research_v2.state import ResearchState, ResearchPhase, create_initial_state
    from config.llm_config import get_config

    # 使用配置创建 CodeWizard 实例
    llm_config = get_config()
    wizard = CodeWizard(
        llm_api_key=llm_config.api_key,
        llm_base_url=llm_config.base_url,
        model=llm_config.agents.wizard.model
    )

    # 使用 create_initial_state 创建完整的状态
    mock_state = create_initial_state("中国GDP增长率分析", "test-session")

    # 设置分析阶段
    mock_state["phase"] = ResearchPhase.ANALYZING.value

    # 添加测试大纲
    mock_state["outline"] = [
        {"id": "sec_1", "title": "GDP增长趋势", "requires_chart": True, "section_type": "quantitative", "description": "分析中国近年GDP增长趋势"}
    ]

    # 添加测试数据点
    mock_state["data_points"] = [
        {"name": "2020年GDP增长率", "value": 2.3, "unit": "%"},
        {"name": "2021年GDP增长率", "value": 8.1, "unit": "%"},
        {"name": "2022年GDP增长率", "value": 3.0, "unit": "%"},
        {"name": "2023年GDP增长率", "value": 5.2, "unit": "%"},
        {"name": "2024年GDP增长率", "value": 5.0, "unit": "%"}
    ]

    # 添加测试事实
    mock_state["facts"] = [
        {
            "id": "fact_1",
            "content": "2020年中国GDP增长率为2.3%，是新冠疫情影响下的低谷",
            "source_url": "http://example.com",
            "source_name": "国家统计局",
            "related_sections": ["sec_1"]
        },
        {
            "id": "fact_2",
            "content": "2021年中国GDP强劲反弹，增长率达到8.1%",
            "source_url": "http://example.com",
            "source_name": "国家统计局",
            "related_sections": ["sec_1"]
        }
    ]

    try:
        # 执行数据分析
        result_state = await wizard.process(mock_state)

        charts = result_state.get("charts", [])
        return {
            "success": True,
            "charts_count": len(charts),
            "charts": [
                {
                    "title": c.get("title", ""),
                    "type": c.get("type", ""),
                    "has_image": bool(c.get("image_base64")),
                    "image_length": len(c.get("image_base64", "")) if c.get("image_base64") else 0
                }
                for c in charts
            ],
            "errors": result_state.get("errors", [])
        }
    except Exception as e:
        logger.error(f"Test wizard error: {e}")
        import traceback
        return {
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }


@router.post("/cancel/{session_id}", status_code=HTTP_200_OK)
async def cancel_research(
    session_id: str,
    current_user: User = Depends(get_current_user_required),
):
    """
    取消正在进行的研究任务

    Args:
        session_id: 会话ID

    Returns:
        取消确认信息
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        info = get_checkpoint_service().get_checkpoint_info(session_id)
        if not info:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail=f"会话 {session_id} 不存在",
            )
        _assert_checkpoint_access(info, current_user)
        # 设置取消标志到 Redis，有效期 5 分钟
        cancel_key = f"{CANCEL_KEY_PREFIX}{session_id}"
        cache.set(cancel_key, {"cancelled": True}, expire=300)
        logger.info(f"Research cancelled for session: {session_id}")
        return {"success": True, "message": "Research cancellation requested"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel research: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


def is_research_cancelled(session_id: str) -> bool:
    """
    检查研究任务是否已被取消

    Args:
        session_id: 会话ID

    Returns:
        是否已取消
    """
    cancel_key = f"{CANCEL_KEY_PREFIX}{session_id}"
    result = cache.get(cancel_key)
    return result is not None and result.get("cancelled", False)


def clear_cancel_flag(session_id: str):
    """
    清除取消标志（研究开始时调用）

    Args:
        session_id: 会话ID
    """
    cancel_key = f"{CANCEL_KEY_PREFIX}{session_id}"
    cache.delete(cancel_key)


@router.get("/reviews", response_model=ReviewTaskListResponse, status_code=HTTP_200_OK)
async def list_pending_reviews(
    current_user: User = Depends(require_human_reviewer),
    db: Session = Depends(get_db),
):
    """List only integrity-verified, non-self pending review work.

    This deliberately does not reuse ``/checkpoints``.  The response is a
    whitelist projection built from sealed business state, while the generic
    endpoint remains owner-scoped and may expose checkpoint metadata needed by
    its legacy UI.
    """
    try:
        tasks = ReviewWorkspaceService(db).list_pending(current_user.id)
        return ReviewTaskListResponse(items=tasks, total=len(tasks))
    except (ReviewTaskNotFound, ReviewTaskNotPending,
            ReviewWorkspaceIntegrityError, ReviewWorkspaceUnavailable) as exc:
        _raise_review_workspace_error(exc)


@router.get("/reviews/{session_id}", response_model=ReviewTaskPacket, status_code=HTTP_200_OK)
async def get_pending_review(
    session_id: str,
    current_user: User = Depends(require_human_reviewer),
    db: Session = Depends(get_db),
):
    """Return one explicit minimum review packet, never a raw checkpoint."""
    try:
        return ReviewWorkspaceService(db).get_pending(session_id, current_user.id)
    except (ReviewTaskNotFound, ReviewTaskNotPending,
            ReviewWorkspaceIntegrityError, ReviewWorkspaceUnavailable) as exc:
        _raise_review_workspace_error(exc)


# ============ 检查点 API ============

@router.get("/checkpoint/{session_id}", status_code=HTTP_200_OK)
async def get_checkpoint(
    session_id: str,
    current_user: User = Depends(get_current_user_required),
):
    """
    获取研究检查点信息

    Args:
        session_id: 会话ID

    Returns:
        检查点信息（不含完整状态）
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        checkpoint_service = get_checkpoint_service()
        info = checkpoint_service.get_checkpoint_info(session_id)
        if info:
            _assert_checkpoint_access(info, current_user)
            return {"success": True, "checkpoint": info}
        return {"success": False, "message": "No checkpoint found"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get checkpoint: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/checkpoint/{session_id}/full", status_code=HTTP_200_OK)
async def get_full_checkpoint(
    session_id: str,
    current_user: User = Depends(get_current_user_required),
):
    """
    获取完整的研究检查点（包含 UI 状态和报告）

    用于恢复未完成的研究状态到前端

    Args:
        session_id: 会话ID

    Returns:
        完整检查点数据，包含:
        - state_json: 后端研究状态
        - ui_state_json: 前端 UI 状态（研究步骤、图表等）
        - final_report: 最终报告内容
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        checkpoint_service = get_checkpoint_service()
        info = checkpoint_service.get_checkpoint_info(session_id)
        if info:
            _assert_checkpoint_access(info, current_user)
        full_data = checkpoint_service.load_full_checkpoint(session_id)
        if full_data:
            return {"success": True, "checkpoint": full_data}
        return {"success": False, "message": "No checkpoint found"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get full checkpoint: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/checkpoints", status_code=HTTP_200_OK)
async def list_checkpoints(
    status: Optional[str] = Query(None, description="过滤状态: running/paused/completed/failed"),
    limit: int = Query(20, ge=1, le=100, description="返回数量限制"),
    current_user: User = Depends(get_current_user_required),
):
    """
    列出研究检查点

    Args:
        status: 状态过滤
        limit: 返回数量限制

    Returns:
        检查点列表
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        checkpoint_service = get_checkpoint_service()
        checkpoints = checkpoint_service.list_checkpoints(
            user_id=None if current_user.is_superuser else str(current_user.id),
            status=status,
            limit=limit,
        )
        return {"success": True, "checkpoints": checkpoints, "total": len(checkpoints)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list checkpoints: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.delete("/checkpoint/{session_id}", status_code=HTTP_200_OK)
async def delete_checkpoint(
    session_id: str,
    current_user: User = Depends(get_current_user_required),
):
    """
    删除研究检查点

    Args:
        session_id: 会话ID

    Returns:
        删除结果
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        checkpoint_service = get_checkpoint_service()
        info = checkpoint_service.get_checkpoint_info(session_id)
        if info:
            _assert_checkpoint_access(info, current_user)
        success = checkpoint_service.delete_checkpoint(session_id)
        if success:
            return {"success": True, "message": "Checkpoint deleted"}
        return {"success": False, "message": "Checkpoint not found"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete checkpoint: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post("/resume/{session_id}", status_code=HTTP_200_OK)
async def resume_research(
    session_id: str,
    current_user: User = Depends(get_current_user_required),
):
    """
    恢复研究任务（从检查点）

    Args:
        session_id: 会话ID

    Returns:
        流式响应，从检查点继续研究
    """
    try:
        from service.checkpoint_service import get_checkpoint_service
        checkpoint_service = get_checkpoint_service()
        info = checkpoint_service.get_checkpoint_info(session_id)

        if not info:
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="No checkpoint found for this session"
            )
        _assert_checkpoint_access(info, current_user)

        if info.get("status") == "completed":
            raise HTTPException(
                status_code=HTTP_400_BAD_REQUEST,
                detail="Research already completed"
            )

        # 使用 V2 服务恢复
        service_v2 = get_research_service_v2()

        async def generate_sse():
            try:
                async for event in service_v2.research(
                    query=info.get("query", ""),
                    session_id=session_id,
                    resume=True,
                    user_id=str(current_user.id),
                ):
                    yield event
            except Exception as e:
                logger.error(f"Resume research error: {e}")
                error_event = serialize_event({"type": "error", "content": str(e)})
                yield f"data: {error_event}\n\n"

        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to resume research: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/review/{session_id}", status_code=HTTP_200_OK)
async def submit_human_review(
    session_id: str,
    request: HumanReviewRequest,
    current_user: User = Depends(require_human_reviewer),
    db: Session = Depends(get_db),
):
    """
    提交风控复核结论，从复核卡点继续执行（v0.6 人机协同）

    业务位置：系统产出尽调报告与风险评级后，若评级要求人工复核
    （`requires_human_review`），流程会在 `human_review` 节点暂停并推出
    `human_review_required` 事件。风控人员在前端确认后调用本端点。

    复核人可以推翻规则引擎的等级（`override_level`），但**规则引擎的原始
    结论会被完整保留并写进报告**——改写而不留痕，出坏账追责时无法区分
    "规则算错了"和"人改过了"。

    Args:
        session_id: 会话ID，与发起尽调时一致
        request: 复核结论；复核人由登录身份确定，不接受客户端指定

    Returns:
        流式响应：从断点继续直到 research_complete
    """
    try:
        try:
            owner_id = ReviewWorkspaceService(db).authorize_submission(
                session_id, current_user.id,
            )
        except (ReviewTaskNotFound, ReviewTaskNotPending,
                ReviewWorkspaceIntegrityError, ReviewWorkspaceUnavailable) as exc:
            _raise_review_workspace_error(exc)

        service_v2 = get_research_service_v2()
        decision = request.model_dump()
        decision["reviewer"] = current_user.username
        decision["reviewer_id"] = str(current_user.id)

        async def generate_sse():
            try:
                async for chunk in service_v2.submit_review(
                    session_id,
                    decision,
                    # The resumed graph persists under the checkpoint owner;
                    # reviewer identity is carried only in the signed decision.
                    user_id=owner_id,
                ):
                    yield chunk
            except Exception as e:
                logger.error(f"Submit review error: {e}")
                yield f"data: {serialize_event({'type': 'error', 'content': str(e)})}\n\n"

        return StreamingResponse(generate_sse(), media_type="text/event-stream")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit human review: {e}")
        raise HTTPException(status_code=HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
