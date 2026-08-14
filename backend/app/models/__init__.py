# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。
#
# 本文件在原课程项目基础上二次开发（已获授权）。
# 改造部分 © 2026 XbhbxZty
from .user import User
from .chat import ChatSession, ChatMessage, ChatAttachment, LongTermMemory
from .knowledge import KnowledgeBase, Document
from .industry_data import IndustryStats, CompanyData, PolicyData
from .research import ResearchCheckpoint
from .news import IndustryNews, BiddingInfo, NewsCollectionTask

__all__ = [
    "User",
    "ChatSession",
    "ChatMessage",
    "ChatAttachment",
    "LongTermMemory",
    "KnowledgeBase",
    "Document",
    "IndustryStats",
    "CompanyData",
    "PolicyData",
    "ResearchCheckpoint",
    "IndustryNews",
    "BiddingInfo",
    "NewsCollectionTask",
]
