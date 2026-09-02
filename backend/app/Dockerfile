FROM python:3.11.7-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Build with backend/ as the Docker context so alembic.ini and migrations are
# included alongside app/.  app_main uses the historical top-level imports.
ENV PYTHONPATH=/app/app:/app


# 配置pip使用清华镜像源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# 复制代码到容器
COPY . /app/

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 暴露端口
EXPOSE 8000

# The runtime guard is read-only; apply migrations explicitly before serving.
CMD ["sh", "-c", "python -m alembic -c /app/alembic.ini upgrade head && exec uvicorn app_main:app --host 0.0.0.0 --port 8000"]
