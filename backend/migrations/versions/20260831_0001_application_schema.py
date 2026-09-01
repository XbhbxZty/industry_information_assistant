"""Establish the complete application schema baseline.

Revision ID: 20260831_0001
Revises: None

This revision is deliberately hand-reviewed and self-contained.  It does not
call application metadata/create_all and does not manage the optional Docker
demo tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260831_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_superuser", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "admin_company_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("credit_code", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("scenario", sa.String(length=32), nullable=False),
        sa.Column("scenario_data", sa.JSON(), nullable=False),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("materials", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("archived_by", sa.String(length=64), nullable=True),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(created_by)) BETWEEN 1 AND 64",
            name="ck_company_profile_created_by_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(updated_by)) BETWEEN 1 AND 64",
            name="ck_company_profile_updated_by_nonblank",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_company_profiles_archived_by",
        "admin_company_profiles",
        ["archived_by"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profiles_created_by",
        "admin_company_profiles",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profiles_credit_code",
        "admin_company_profiles",
        ["credit_code"],
        unique=True,
    )
    op.create_index(
        "ix_admin_company_profiles_name",
        "admin_company_profiles",
        ["name"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profiles_status",
        "admin_company_profiles",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profiles_updated_by",
        "admin_company_profiles",
        ["updated_by"],
        unique=False,
    )

    op.create_table(
        "admin_company_profile_audits",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(actor_id)) BETWEEN 1 AND 64",
            name="ck_company_profile_audit_actor_nonblank",
        ),
        sa.CheckConstraint(
            "length(trim(change_reason)) BETWEEN 1 AND 500",
            name="ck_company_profile_audit_reason_nonblank",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_company_profile_audit_revision_positive",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "revision",
            name="uq_company_profile_audit_revision",
        ),
    )
    op.create_index(
        "ix_admin_company_profile_audits_actor_id",
        "admin_company_profile_audits",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profile_audits_created_at",
        "admin_company_profile_audits",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profile_audits_profile_id",
        "admin_company_profile_audits",
        ["profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_admin_company_profile_audits_revision",
        "admin_company_profile_audits",
        ["revision"],
        unique=False,
    )

    op.create_table(
        "industry_stats",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "industry_name",
            sa.String(length=100),
            nullable=False,
            comment="行业名称",
        ),
        sa.Column(
            "metric_name",
            sa.String(length=100),
            nullable=False,
            comment="指标名称",
        ),
        sa.Column("metric_value", sa.Float(), nullable=False, comment="指标值"),
        sa.Column("unit", sa.String(length=50), nullable=True, comment="单位"),
        sa.Column("year", sa.Integer(), nullable=True, comment="年份"),
        sa.Column("quarter", sa.Integer(), nullable=True, comment="季度(1-4)"),
        sa.Column("month", sa.Integer(), nullable=True, comment="月份(1-12)"),
        sa.Column(
            "region",
            sa.String(length=50),
            nullable=True,
            comment="地区",
        ),
        sa.Column(
            "source",
            sa.String(length=200),
            nullable=True,
            comment="数据来源",
        ),
        sa.Column("source_url", sa.Text(), nullable=True, comment="来源链接"),
        sa.Column("notes", sa.Text(), nullable=True, comment="备注说明"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_industry_stats_industry_name",
        "industry_stats",
        ["industry_name"],
        unique=False,
    )
    op.create_index(
        "ix_industry_stats_metric_name",
        "industry_stats",
        ["metric_name"],
        unique=False,
    )
    op.create_index(
        "ix_industry_stats_year",
        "industry_stats",
        ["year"],
        unique=False,
    )

    op.create_table(
        "company_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "company_name",
            sa.String(length=200),
            nullable=False,
            comment="企业名称",
        ),
        sa.Column(
            "stock_code",
            sa.String(length=20),
            nullable=True,
            comment="股票代码",
        ),
        sa.Column(
            "industry",
            sa.String(length=100),
            nullable=True,
            comment="所属行业",
        ),
        sa.Column(
            "sub_industry",
            sa.String(length=100),
            nullable=True,
            comment="细分行业",
        ),
        sa.Column("revenue", sa.Float(), nullable=True, comment="营收(亿元)"),
        sa.Column("net_profit", sa.Float(), nullable=True, comment="净利润(亿元)"),
        sa.Column("gross_margin", sa.Float(), nullable=True, comment="毛利率(%)"),
        sa.Column("market_cap", sa.Float(), nullable=True, comment="市值(亿元)"),
        sa.Column("employees", sa.Integer(), nullable=True, comment="员工数"),
        sa.Column("market_share", sa.Float(), nullable=True, comment="市场份额(%)"),
        sa.Column("year", sa.Integer(), nullable=True, comment="年份"),
        sa.Column("quarter", sa.Integer(), nullable=True, comment="季度"),
        sa.Column(
            "data_source",
            sa.String(length=200),
            nullable=True,
            comment="数据来源",
        ),
        sa.Column(
            "extra_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="扩展数据",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_data_company_name",
        "company_data",
        ["company_name"],
        unique=False,
    )
    op.create_index(
        "ix_company_data_industry",
        "company_data",
        ["industry"],
        unique=False,
    )
    op.create_index(
        "ix_company_data_year",
        "company_data",
        ["year"],
        unique=False,
    )

    op.create_table(
        "policy_data",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "policy_name",
            sa.String(length=500),
            nullable=False,
            comment="政策名称",
        ),
        sa.Column(
            "policy_number",
            sa.String(length=100),
            nullable=True,
            comment="政策文号",
        ),
        sa.Column(
            "department",
            sa.String(length=200),
            nullable=False,
            comment="发布部门",
        ),
        sa.Column(
            "level",
            sa.String(length=50),
            nullable=True,
            comment="政策级别(国家级/省级/市级)",
        ),
        sa.Column("publish_date", sa.Date(), nullable=True, comment="发布日期"),
        sa.Column("effective_date", sa.Date(), nullable=True, comment="生效日期"),
        sa.Column("expiry_date", sa.Date(), nullable=True, comment="失效日期"),
        sa.Column(
            "category",
            sa.String(length=100),
            nullable=True,
            comment="政策类别",
        ),
        sa.Column(
            "industry",
            sa.String(length=100),
            nullable=True,
            comment="相关行业",
        ),
        sa.Column("summary", sa.Text(), nullable=True, comment="政策摘要"),
        sa.Column(
            "key_points",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="关键要点",
        ),
        sa.Column("full_text_url", sa.Text(), nullable=True, comment="全文链接"),
        sa.Column(
            "impact_level",
            sa.String(length=20),
            nullable=True,
            comment="影响程度(重大/一般/轻微)",
        ),
        sa.Column(
            "affected_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="受影响主体",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_data_category",
        "policy_data",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_policy_data_department",
        "policy_data",
        ["department"],
        unique=False,
    )
    op.create_index(
        "ix_policy_data_industry",
        "policy_data",
        ["industry"],
        unique=False,
    )
    op.create_index(
        "ix_policy_data_publish_date",
        "policy_data",
        ["publish_date"],
        unique=False,
    )

    op.create_table(
        "research_checkpoint_integrities",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("integrity_version", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("business_revision", sa.Integer(), nullable=False),
        sa.Column("business_seal", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "industry_news",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "industry_id",
            sa.String(length=50),
            nullable=True,
            comment="行业ID",
        ),
        sa.Column(
            "title",
            sa.String(length=500),
            nullable=False,
            comment="资讯标题",
        ),
        sa.Column("content", sa.Text(), nullable=True, comment="资讯内容/摘要"),
        sa.Column(
            "source",
            sa.String(length=200),
            nullable=True,
            comment="来源",
        ),
        sa.Column("source_url", sa.Text(), nullable=True, comment="来源链接"),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=True,
            comment="分类：政策/纪要/研报/新闻",
        ),
        sa.Column(
            "department",
            sa.String(length=200),
            nullable=True,
            comment="发布部门/机构",
        ),
        sa.Column("publish_time", sa.DateTime(), nullable=True, comment="发布时间"),
        sa.Column("collected_at", sa.DateTime(), nullable=True, comment="采集时间"),
        sa.Column(
            "keywords",
            sa.String(length=500),
            nullable=True,
            comment="关键词",
        ),
        sa.Column("is_read", sa.Boolean(), nullable=True, comment="是否已读"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_industry_news_category",
        "industry_news",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_industry_news_industry_id",
        "industry_news",
        ["industry_id"],
        unique=False,
    )
    op.create_index(
        "ix_industry_news_publish_time",
        "industry_news",
        ["publish_time"],
        unique=False,
    )

    op.create_table(
        "bidding_info",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "industry_id",
            sa.String(length=50),
            nullable=True,
            comment="行业ID",
        ),
        sa.Column(
            "bid_id",
            sa.String(length=100),
            nullable=True,
            comment="招投标项目ID",
        ),
        sa.Column(
            "title",
            sa.String(length=500),
            nullable=False,
            comment="项目标题",
        ),
        sa.Column(
            "notice_type",
            sa.String(length=50),
            nullable=True,
            comment="公告类型：招标/中标/采购等",
        ),
        sa.Column(
            "province",
            sa.String(length=50),
            nullable=True,
            comment="省份",
        ),
        sa.Column("city", sa.String(length=50), nullable=True, comment="城市"),
        sa.Column("content", sa.Text(), nullable=True, comment="详细内容"),
        sa.Column("publish_time", sa.DateTime(), nullable=True, comment="发布时间"),
        sa.Column(
            "source",
            sa.String(length=200),
            nullable=True,
            comment="数据来源",
        ),
        sa.Column("collected_at", sa.DateTime(), nullable=True, comment="采集时间"),
        sa.Column("is_read", sa.Boolean(), nullable=True, comment="是否已读"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bidding_info_bid_id",
        "bidding_info",
        ["bid_id"],
        unique=True,
    )
    op.create_index(
        "ix_bidding_info_industry_id",
        "bidding_info",
        ["industry_id"],
        unique=False,
    )
    op.create_index(
        "ix_bidding_info_notice_type",
        "bidding_info",
        ["notice_type"],
        unique=False,
    )
    op.create_index(
        "ix_bidding_info_province",
        "bidding_info",
        ["province"],
        unique=False,
    )
    op.create_index(
        "ix_bidding_info_publish_time",
        "bidding_info",
        ["publish_time"],
        unique=False,
    )

    op.create_table(
        "news_collection_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_type",
            sa.String(length=50),
            nullable=False,
            comment="任务类型：news/bidding",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=True,
            comment="状态：pending/running/completed/failed",
        ),
        sa.Column(
            "total_collected",
            sa.Integer(),
            nullable=True,
            comment="采集数量",
        ),
        sa.Column("error_message", sa.Text(), nullable=True, comment="错误信息"),
        sa.Column("started_at", sa.DateTime(), nullable=True, comment="开始时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="完成时间"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("session_type", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("document_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "research_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column(
            "state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "ui_state_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("final_report", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_checkpoints_session_id",
        "research_checkpoints",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("thinking", sa.Text(), nullable=True),
        sa.Column(
            "references_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "image_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "long_term_memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "key_insights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("milvus_ids", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "chat_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=50), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("chat_attachments")
    op.drop_table("long_term_memories")
    op.drop_table("documents")
    op.drop_table("chat_messages")
    op.drop_table("research_checkpoints")
    op.drop_table("knowledge_bases")
    op.drop_table("chat_sessions")
    op.drop_table("news_collection_tasks")
    op.drop_table("bidding_info")
    op.drop_table("industry_news")
    op.drop_table("research_checkpoint_integrities")
    op.drop_table("policy_data")
    op.drop_table("company_data")
    op.drop_table("industry_stats")
    op.drop_table("admin_company_profile_audits")
    op.drop_table("admin_company_profiles")
    op.drop_table("users")
