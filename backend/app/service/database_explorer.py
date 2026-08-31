# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
"""数据库探索服务 - 仅支持 PostgreSQL。"""
import re
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


# 该页面是行业演示数据浏览器，不是通用数据库管理器。服务端必须独立维护
# 允许范围，不能依赖前端隐藏敏感表。
ALLOWED_DATABASE_TABLES = frozenset({
    "industry_stats",
    "company_data",
    "policy_data",
})

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_FORBIDDEN_QUERY_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "GRANT",
    "REVOKE",
    "COPY",
    "CALL",
    "DO",
    "EXECUTE",
    "PREPARE",
    "LOCK",
    "TABLE",
    "CURRENT_USER",
    "SESSION_USER",
    "CURRENT_ROLE",
    "CURRENT_CATALOG",
    "CURRENT_SCHEMA",
)
_ALLOWED_QUERY_FUNCTIONS = frozenset({
    "COUNT",
    "SUM",
    "AVG",
    "MAX",
    "MIN",
    "COALESCE",
    "NULLIF",
    "CAST",
    "ROUND",
    "DATE",
    "YEAR",
    "MONTH",
    "IN",
    "EXISTS",
})


def require_allowed_database_table(table_name: str) -> str:
    """Return a trusted table name or reject it before any SQL is built."""
    if table_name not in ALLOWED_DATABASE_TABLES:
        raise ValueError("Table is not available in the database explorer")
    return table_name


def validate_allowed_select(sql: str) -> str:
    """Validate the explorer's deliberately small, single-table SELECT subset.

    This is intentionally not a general SQL sandbox. Complex forms (CTEs,
    joins, unions and subqueries) fail closed so every accepted query has one
    statically identifiable relation from ``ALLOWED_DATABASE_TABLES``.
    """
    statement = (sql or "").strip()
    if not statement:
        raise ValueError("SQL query is empty")
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()

    masked = _STRING_LITERAL.sub(lambda match: " " * len(match.group(0)), statement)
    if "'" in masked:
        raise ValueError("Unterminated SQL string literal")
    if ";" in masked or "--" in masked or "/*" in masked or "*/" in masked:
        raise ValueError("Multiple statements and SQL comments are not allowed")
    if "$" in masked:
        raise ValueError("Dollar-quoted SQL and dollar identifiers are not allowed")

    upper = masked.upper()
    if not re.match(r"^SELECT\b", upper):
        raise ValueError("Only SELECT queries are allowed")
    if len(re.findall(r"\bSELECT\b", upper)) != 1:
        raise ValueError("Subqueries are not allowed")
    if len(re.findall(r"\bFROM\b", upper)) != 1:
        raise ValueError("A query must read from exactly one allowed table")

    for keyword in _FORBIDDEN_QUERY_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            raise ValueError(f"Query contains forbidden keyword: {keyword}")
    if re.search(r"\b(?:WITH|UNION|INTERSECT|EXCEPT|JOIN)\b", upper):
        raise ValueError("CTEs, set operations and joins are not allowed")
    if re.search(r'"(?:""|[^"])+"\s*\(', masked):
        raise ValueError("Quoted or dynamic SQL functions are not allowed")
    for function_name in re.findall(r'(?<![\w"])([^\W\d]\w*)\s*\(', upper):
        if function_name not in _ALLOWED_QUERY_FUNCTIONS:
            raise ValueError(f"Query function is not allowed: {function_name}")

    from_match = re.search(
        r"\bFROM\s+(.*?)(?=\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|"
        r"\bORDER\s+BY\b|\bLIMIT\b|\bOFFSET\b|$)",
        masked,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not from_match:
        raise ValueError("A query must read from exactly one allowed table")

    relation = from_match.group(1).strip()
    relation_match = re.fullmatch(
        rf'(?:"(?P<quoted>{_IDENTIFIER})"|(?P<plain>{_IDENTIFIER}))'
        rf'(?:\s+(?:AS\s+)?{_IDENTIFIER})?',
        relation,
        flags=re.IGNORECASE,
    )
    if not relation_match:
        raise ValueError("Only one unqualified database-explorer table is allowed")

    table_name = relation_match.group("quoted") or relation_match.group("plain")
    require_allowed_database_table(table_name.lower())
    return statement


class DatabaseExplorer:
    """数据库探索器 - 提供只读查询功能"""

    def __init__(self, db: Session):
        self.db = db

    def get_tables(self) -> List[Dict[str, Any]]:
        """只获取数据库探索页允许展示的业务表。"""
        query = text("""
            SELECT
                table_name,
                pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size,
                (SELECT count(*) FROM information_schema.columns
                 WHERE table_name = t.table_name AND table_schema = 'public') as column_count
            FROM information_schema.tables t
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE'
            AND table_name IN ('industry_stats', 'company_data', 'policy_data')
            ORDER BY table_name
        """)
        result = self.db.execute(query)
        tables = []
        for row in result:
            # 获取行数（单独查询以避免复杂嵌套）
            count_query = text(f'SELECT count(*) FROM "{row.table_name}"')
            try:
                count_result = self.db.execute(count_query)
                row_count = count_result.scalar()
            except Exception:
                self.db.rollback()
                row_count = 0

            tables.append({
                "name": row.table_name,
                "size": row.size,
                "column_count": row.column_count,
                "row_count": row_count,
            })
        return tables

    def get_table_schema(self, table_name: str) -> Dict[str, Any]:
        """获取表结构"""
        table_name = require_allowed_database_table(table_name)

        # 获取列信息
        columns_query = text("""
            SELECT
                column_name,
                data_type,
                character_maximum_length,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            ORDER BY ordinal_position
        """)
        columns_result = self.db.execute(columns_query, {"table_name": table_name})
        columns = []
        for row in columns_result:
            columns.append({
                "name": row.column_name,
                "type": row.data_type,
                "max_length": row.character_maximum_length,
                "nullable": row.is_nullable == "YES",
                "default": row.column_default,
            })

        # 获取主键信息
        pk_query = text("""
            SELECT a.attname
            FROM pg_index i
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = :table_name::regclass AND i.indisprimary
        """)
        try:
            pk_result = self.db.execute(pk_query, {"table_name": table_name})
            primary_keys = [row.attname for row in pk_result]
        except Exception:
            self.db.rollback()
            primary_keys = []

        # 获取索引信息
        idx_query = text("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = :table_name AND schemaname = 'public'
        """)
        try:
            idx_result = self.db.execute(idx_query, {"table_name": table_name})
            indexes = []
            for row in idx_result:
                indexes.append({
                    "name": row.indexname,
                    "definition": row.indexdef,
                })
        except Exception:
            self.db.rollback()
            indexes = []

        return {
            "table_name": table_name,
            "columns": columns,
            "primary_keys": primary_keys,
            "indexes": indexes,
        }

    def get_table_data(
        self,
        table_name: str,
        limit: int = 100,
        offset: int = 0,
        order_by: Optional[str] = None,
        order_dir: str = "asc"
    ) -> Dict[str, Any]:
        """获取表数据（分页）"""
        table_name = require_allowed_database_table(table_name)

        # 验证排序方向
        order_dir = order_dir.lower()
        if order_dir not in ("asc", "desc"):
            order_dir = "asc"

        # 获取总行数
        count_query = text(f'SELECT count(*) FROM "{table_name}"')
        total = self.db.execute(count_query).scalar()

        # 构建查询（使用安全的参数化）
        if order_by and self._is_valid_identifier(order_by):
            data_query = text(f'SELECT * FROM "{table_name}" ORDER BY "{order_by}" {order_dir} LIMIT :limit OFFSET :offset')
        else:
            data_query = text(f'SELECT * FROM "{table_name}" LIMIT :limit OFFSET :offset')

        result = self.db.execute(data_query, {"limit": limit, "offset": offset})

        # 获取列名
        columns = list(result.keys())

        # 获取数据
        rows = []
        for row in result:
            row_dict = {}
            for i, col in enumerate(columns):
                value = row[i]
                # 处理特殊类型
                if hasattr(value, 'isoformat'):
                    value = value.isoformat()
                elif isinstance(value, bytes):
                    value = f"<binary {len(value)} bytes>"
                row_dict[col] = value
            rows.append(row_dict)

        return {
            "table_name": table_name,
            "columns": columns,
            "rows": rows,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def execute_query(self, sql: str, limit: int = 100) -> Dict[str, Any]:
        """执行数据库探索页支持的有限只读查询。"""
        sql = validate_allowed_select(sql)

        # 添加 LIMIT 如果没有
        if not re.search(r"\bLIMIT\b", sql, flags=re.IGNORECASE):
            sql = f"{sql} LIMIT {limit}"

        try:
            result = self.db.execute(text(sql))
            columns = list(result.keys())

            rows = []
            for row in result:
                row_dict = {}
                for i, col in enumerate(columns):
                    value = row[i]
                    if hasattr(value, 'isoformat'):
                        value = value.isoformat()
                    elif isinstance(value, bytes):
                        value = f"<binary {len(value)} bytes>"
                    row_dict[col] = value
                rows.append(row_dict)

            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            }
        except Exception as e:
            self.db.rollback()
            logger.error(f"Query execution error: {e}")
            raise ValueError(f"Query execution failed: {str(e)}")

    def _is_valid_identifier(self, name: str) -> bool:
        """验证标识符是否安全"""
        if not name:
            return False
        # 只允许字母、数字、下划线
        import re
        return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name))
