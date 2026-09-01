# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""Core public exports, loaded lazily so migration helpers stay import-safe."""
from __future__ import annotations

from importlib import import_module

__all__ = [
    "get_db",
    "SessionLocal",
    "engine",
    "Base",
    "DATABASE_URL",
    "PSYCOPG_CONNINFO",
    "DatabaseConnectionUrls",
    "DatabaseUrlConfigurationError",
    "resolve_database_urls",
    "verify_password",
    "get_password_hash",
    "create_access_token",
    "decode_token",
    "Token",
    "TokenData",
    "cache",
    "get_redis_client",
    "RedisCache",
]

_EXPORT_MODULES = {
    "get_db": "database",
    "SessionLocal": "database",
    "engine": "database",
    "Base": "database",
    "DATABASE_URL": "database",
    "PSYCOPG_CONNINFO": "database",
    "DatabaseConnectionUrls": "database_url",
    "DatabaseUrlConfigurationError": "database_url",
    "resolve_database_urls": "database_url",
    "verify_password": "security",
    "get_password_hash": "security",
    "create_access_token": "security",
    "decode_token": "security",
    "Token": "security",
    "TokenData": "security",
    "cache": "redis_client",
    "get_redis_client": "redis_client",
    "RedisCache": "redis_client",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value
