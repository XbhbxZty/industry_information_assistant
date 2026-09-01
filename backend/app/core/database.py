# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""数据库连接和会话管理"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .database_url import resolve_database_urls

# Keep DATABASE_URL as the synchronous SQLAlchemy URL for existing callers.
# psycopg3 users must use the parallel URL without a SQLAlchemy driver suffix.
DATABASE_CONNECTION_URLS = resolve_database_urls()
DATABASE_URL = DATABASE_CONNECTION_URLS.sqlalchemy_url
PSYCOPG_CONNINFO = DATABASE_CONNECTION_URLS.psycopg_conninfo

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """获取数据库会话的依赖函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
