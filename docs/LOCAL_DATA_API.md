# 本地公开尽调数据 API

该 API 只读取独立下载目录 data/public_dd，不会把仓库中的
backend/eval/real_cases、real_cases_processed、现有 PDF 或历史测试产物作为数据源。

## 当前快照

- FinDocResearch：80 家公司、160 条年报元数据；其中 10 家中国公司
  FY2023/FY2024 共 20 份原始年报已下载并校验。
- FinAR-Bench：100 家 A 股公司、100 份财务报表 PDF、100 份规范化财务样本、
  1,300 条任务（fact 600、indicator 600、reasoning 100）。
- LawDual-Bench：1,389 条内幕交易案例描述及对应结构化金标准，
  其中可能包含个人信息，生产暴露前应加访问控制与脱敏。
- FinDeepResearch：64 家公司的研究分析框架。

所有 Hugging Face 仓库均锁定到固定 revision。下载文件的大小与 SHA-256
记录在 data/public_dd/manifests/acquisition.json，20 份中国年报记录在
data/public_dd/manifests/findoc_reports.json。

## 构建

在仓库根目录执行：

    # 已有 raw 数据时，仅重新解压、规范化和生成快照
    python backend/eval/build_local_dataset.py

    # 从固定 revision 重放完整下载，然后规范化
    python backend/eval/build_local_dataset.py --download-repositories --download-findoc-reports

默认目录可通过 --root 改写。服务端可通过 LOCAL_DATA_ROOT 指向另一个同结构快照。

## 启动和鉴权

路由已注册到现有 FastAPI 应用，前缀为 /local-data。如果设置
LOCAL_DATA_API_KEY，请求必须携带 X-Local-Data-Key；未设置时不额外鉴权，
因此建议只监听回环地址或 Docker 内网。

当前根目录 docker-compose.yml 只启动 PostgreSQL、Redis、Milvus 等基础设施，
不包含后端 API 容器。本地 API 可用 start-app.ps1 启动，或先将应用数据库升级到
Alembic head 后进入 backend/app 执行：

    cd backend && python -m alembic upgrade head
    cd app && uvicorn app_main:app --host 127.0.0.1 --port 8000

该数据 API 本身不依赖 PostgreSQL 或 Milvus；若以后放入容器，请将宿主机
data/public_dd 只读挂载进容器，并把 LOCAL_DATA_ROOT 指向容器内挂载路径。

主要接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /local-data/health | 快照状态与计数 |
| GET | /local-data/datasets | 数据源、revision、许可证 |
| GET | /local-data/companies | 公司检索与过滤 |
| GET | /local-data/companies/resolve?query= | 名称或股票代码解析 |
| GET | /local-data/documents | 年报/财务报表文档 |
| GET | /local-data/documents/{document_id}/file | 下载本地原始 PDF |
| GET | /local-data/financial-samples | 规范化财务报表文本 |
| GET | /local-data/tasks | 财务抽取、指标、推理任务及标准答案 |
| GET | /local-data/legal-cases | 法律案例描述 |
| GET | /local-data/research-structures | 深度研究框架 |
| POST | /local-data/search | 跨域统一检索 |

统一搜索请求包含 query、domains 和 top_k。例如 domains 可设置为
task、legal_case、research_structure。

## 边界与局限

- FinAR 的 PDF 是财务报表节选，不是完整年报。
- FinDoc 只有中国市场的 20 份年报在当前快照中本地可用；其余 140 条保留
  原始 URL 和 available_locally=false。
- LawDual 的 1,389 个 processed/entry_*.json 作为官方结构化标注原样保留；
  API 不会把 schema 占位符误当作标注结果。
- FinDeep 的 Markdown 是分析任务结构，不是经核验的公司事实。
