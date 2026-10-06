# syntax=docker/dockerfile:1
# LogiOps 后端镜像（多阶段：builder 只装依赖，runtime 只带运行所需文件）
#
# 构建（构建上下文必须是 backend/）：
#     docker build -t logiops-backend:local backend
#     # 或从仓库根目录：docker compose build backend
# 运行：
#     docker run --rm -p 8000:8000 \
#       -e DATABASE_URL="mysql+pymysql://logiops:logiops@host.docker.internal:3306/logiops?charset=utf8mb4" \
#       logiops-backend:local
#
# ⚠️ 本机未安装 Docker（基线附录 D），本文件**未实际构建验证**，仅做静态自查：
#     docker-compose.yml / Dockerfile 的 YAML、语法与路径均已核对。

# ---------------------------------------------------------------------------
# 阶段 1：依赖层（uv 0.12 + uv.lock 锁版本，基线 §4）
# 只复制 pyproject.toml / uv.lock，避免把宿主机 .venv 与 __pycache__ 拷进镜像
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

# 国内网络：默认走阿里云 PyPI 镜像（实测 pypi.org 直连很慢/易超时）。
# 想用官方源：docker compose build --build-arg UV_INDEX_URL=https://pypi.org/simple/
ARG UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_INDEX_URL=${UV_INDEX_URL} \
    UV_DEFAULT_INDEX=${UV_INDEX_URL} \
    PIP_INDEX_URL=${UV_INDEX_URL}

WORKDIR /build

COPY pyproject.toml uv.lock ./

# --frozen：严格按 uv.lock 安装，CI 与本地一致；--no-dev：运行镜像不带 pytest/ruff/mypy
# [tool.uv] package = false → 本仓库不安装自身为包，这里只装第三方依赖
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---------------------------------------------------------------------------
# 阶段 2：运行层（python:3.12-slim，非 root 用户）
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    AI_REPLAY_DIR=tests/fixtures/ai \
    CLOCK_MODE=system \
    AI_MODE=replay

# curl 仅用于容器内 healthcheck / 手工排查
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --create-home --uid 10001 --shell /usr/sbin/nologin logiops

WORKDIR /app

# 依赖虚拟环境（含 uvicorn / alembic 可执行入口）
COPY --from=builder /opt/venv /opt/venv

# 应用代码 + 迁移 + 测试 fixtures（AI_MODE=replay 需要 fixtures，见基线 §11.3）
COPY --chown=logiops:logiops app ./app
COPY --chown=logiops:logiops alembic ./alembic
COPY --chown=logiops:logiops alembic.ini ./alembic.ini
COPY --chown=logiops:logiops tests ./tests

USER logiops

EXPOSE 8000

# 容器内健康检查：走带前缀的健康接口（`/healthz` 是 404，别写错）
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/healthz || exit 1

# 先迁移后启动（compose 里也用同一条命令；若 alembic/versions 为空则等价于无操作）
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
