# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from router import document_router, search_router, chat_router, research_router, local_data_router
from router.auth_router import router as auth_router
from router.session_router import router as session_router
from router.knowledge_router import router as knowledge_router
from router.attachment_router import router as attachment_router
from router.memory_router import router as memory_router
from router.database_router import router as database_router
from router.news_router import router as news_router
from router.company_profile_router import router as company_profile_router
from core.database import engine
from core.schema_head_guard import assert_database_schema_at_head


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("应用启动中...")
    assert_database_schema_at_head(engine)
    logger.info("Alembic schema head guard passed")

    # 初始化定时任务调度器并检查数据
    try:
        from service.scheduler_service import init_scheduler_and_check_data
        await init_scheduler_and_check_data()
        logger.info("定时任务调度器启动成功")
    except Exception as e:
        logger.error(f"定时任务调度器启动失败: {e}")

    yield

    # 关闭时执行
    logger.info("应用关闭中...")
    try:
        from service.scheduler_service import get_scheduler_service
        scheduler = get_scheduler_service()
        scheduler.stop()
    except Exception as e:
        logger.error(f"定时任务调度器关闭失败: {e}")


app = FastAPI(
    title="行业信息助手 API",
    description="基于 AI Agent 的行业信息助手系统",
    version="2.0.0",
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# 注册路由
app.include_router(auth_router)
app.include_router(session_router)
app.include_router(knowledge_router)
app.include_router(attachment_router)
app.include_router(memory_router)
app.include_router(database_router)
app.include_router(document_router)
app.include_router(search_router)
app.include_router(chat_router)
app.include_router(research_router)
app.include_router(news_router)
app.include_router(company_profile_router)
app.include_router(local_data_router.router)

@app.get("/hello")
async def hello_world():
    """
    Simple hello world endpoint for network verification
    """
    return {
        "status": "success",
        "message": "Hello World! The API is working correctly."
    }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
