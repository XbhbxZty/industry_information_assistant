# 项目启动
## 启动中间件
cd backend
docker compose -f docker-compose-base.yml up -d

### 查看中间件状态
```sh
$ docker ps
CONTAINER ID   IMAGE                                    COMMAND                  CREATED          STATUS                             PORTS                              NAMES
...            redis:7-alpine                           "docker-entrypoint.s…"   ...              Up                                 0.0.0.0:6379->6379/tcp             document-redis
...            milvusdb/milvus:v2.3.3                   "milvus run standalo…"   ...              Up                                 0.0.0.0:29530->19530/tcp           milvus-standalone
...            minio/minio:RELEASE.2023-03-20T20-16-18Z "minio server /minio…"   ...              Up                                 0.0.0.0:9000-9001->9000-9001/tcp   milvus-minio
...            quay.io/coreos/etcd:v3.5.5               "etcd -advertise-cli…"   ...              Up                                                                    milvus-etcd
```

## 安装python依赖包
pip install -r requirements.txt

## 修改env文件
填入个人的DASHSCOPE_API_KEY，SERPER_API_KEY
SERPER_API_KEY获取方法参考：https://serper.dev/

配置 Milvus 连接（可选，默认 localhost:29530）：
```
MILVUS_HOST=localhost
MILVUS_PORT=29530
```

配置 DocMind 文档解析服务：
```
DOCMIND_ACCESS_KEY_ID=your_access_key_id
DOCMIND_ACCESS_KEY_SECRET=your_access_key_secret
```

# 临时添加环境变量
# 用您的百炼API Key代替YOUR_DASHSCOPE_API_KEY
export DASHSCOPE_API_KEY="YOUR_DASHSCOPE_API_KEY"

## 启动后端服务
先显式升级应用 schema；启动时只会只读校验当前数据库是否已经处于本仓库的
Alembic head，不会自动建表、迁移或 stamp：

```sh
python -m alembic upgrade head
```

对于旧库，不能用 `alembic stamp` 跳过迁移；应先走经过评审的 legacy adoption
流程。`0002` 会校验并锚定已有企业档案；有档案时，升级前必须配置 `.env.example`
中的 `COMPANY_PROFILE_AUDIT_KEYS_JSON` 和 `COMPANY_PROFILE_AUDIT_ACTIVE_KEY_ID`。
空库可先升级，但缺少密钥时档案功能会返回 503。详见 [迁移说明](migrations/README.md)。
然后启动服务：

```sh
python app/app_main.py
```


# 接口测试
### 上传文档,用于本地知识库的查询
```sh
cd backend
curl -X POST "http://localhost:8000/documents/upload"   -H "Content-Type: multipart/form-data"   -F "file=@./test/test_doc.pdf"

{"status":"success","message":"成功处理 25 个切片","document_count":25}
```

### 创建会话
```sh
curl -s -X POST http://localhost:8000/chat/session

{"session_id":"02c32f19-b7f0-42ea-b3c1-7d2bc148c21b","created_at":1751194296,"updated_at":1751194296,"message_count":0}
```

### 问答
```sh
curl -N -X POST http://localhost:8000/chat/completion \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d "{
    \"session_id\": \"02c32f19-b7f0-42ea-b3c1-7d2bc148c21b\",
    \"question\": \"如何解决dify_setups表不存在的问题？\"
  }"

```


### deepseach
```sh
curl -N -X POST "http://localhost:8000/research/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "安责险在矿山行业的应用现状、面临的主要挑战以及改进建议有哪些？",
    "max_iterations": 2
  }'

```
