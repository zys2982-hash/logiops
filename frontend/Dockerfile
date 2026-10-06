# syntax=docker/dockerfile:1
# LogiOps 前端镜像（多阶段：node:24-alpine + corepack pnpm 构建 → nginx:alpine 托管）
#
# 构建（构建上下文必须是 frontend/）：
#     docker build -t logiops-frontend:local frontend
#     # 或从仓库根目录：docker compose build frontend
# 运行：容器 80 端口对外映射为 5173，/api 反向代理到 compose 服务名 backend:8000
#
# ⚠️ 本机未安装 Docker（基线附录 D），本文件**未实际构建验证**。
# 前置：仓库里需要有 pnpm-lock.yaml（在 frontend/ 下执行一次 `corepack pnpm install` 会生成）；
#      若缺少 lock 文件，下面的 install 会自动回退到 --no-frozen-lockfile（仅便于首次构建，
#      正式提交请把 pnpm-lock.yaml 一并提交，保证 CI 可复现）。

# ---------------------------------------------------------------------------
# 阶段 1：构建（Node 24 + corepack 自带的 pnpm，无需全局安装）
# ---------------------------------------------------------------------------
FROM node:24-alpine AS builder

# corepack 由 Node 自带，用来固定 packageManager 声明的 pnpm 版本
RUN corepack enable

WORKDIR /app

# 仅先拷贝清单文件，让依赖层可缓存
COPY package.json pnpm-lock.yaml* .npmrc* ./

# 国内网络：默认走 npmmirror（实测 registry.npmjs.org 直连很慢/易超时）。
# 想用官方源：docker compose build --build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com

# --frozen-lockfile 优先（可复现）；无 lock 文件时回退，避免首次构建直接失败
# 注：镜像站是原样透传，lockfile 里的 integrity 校验依然通过
RUN corepack pnpm config set registry "$NPM_REGISTRY" \
 && (corepack pnpm install --frozen-lockfile \
     || corepack pnpm install --no-frozen-lockfile)

# 拷贝源码后构建（Vite 6 产物默认在 dist/）
COPY . .

ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN corepack pnpm run build

# ---------------------------------------------------------------------------
# 阶段 2：运行（nginx 静态托管 + SPA history 回退 + /api 反向代理）
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

# 直接用 printf 写 nginx 站点配置，避免为一个 10 行的代理配置新增仓库文件
RUN set -eux; \
    printf '%s\n' \
      'server {' \
      '    listen 80;' \
      '    server_name _;' \
      '    root /usr/share/nginx/html;' \
      '    index index.html;' \
      '' \
      '    # 压缩：nginx 默认只 gzip text/html，JS/CSS 不加压会原样发出去' \
      '    # （实测：主 JS 1316KB / CSS 356KB → gzip 后 423KB / 48KB，一次访问 1.75MB → 0.49MB）' \
      '    gzip on;' \
      '    gzip_comp_level 5;' \
      '    gzip_min_length 1024;' \
      '    gzip_proxied any;' \
      '    gzip_types text/plain text/css application/javascript application/json application/xml image/svg+xml;' \
      '' \
      '    # 带 hash 的静态资源可长缓存：文件名变了才会重新下载（重复访问几乎不耗流量）' \
      '    location /assets/ {' \
      '        add_header Cache-Control "public, max-age=31536000, immutable";' \
      '    }' \
      '' \
      '    # Vue Router history 模式：未知路径回退到 index.html' \
      '    location / {' \
      '        try_files $uri $uri/ /index.html;' \
      '    }' \
      '' \
      '    # 前端统一走 /api/v1（基线 §10.1），其余保持默认行为' \
      '    location /api/ {' \
      '        proxy_pass http://backend:8000;' \
      '        proxy_http_version 1.1;' \
      '        proxy_set_header Host $host;' \
      '        proxy_set_header X-Real-IP $remote_addr;' \
      '        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
      '        proxy_set_header X-Forwarded-Proto $scheme;' \
      '        proxy_read_timeout 120s;' \
      '    }' \
      '' \
      '    # 接口文档也走同一个端口：http://<站点>/docs（Swagger 会再取 /openapi.json）' \
      '    location /docs {' \
      '        proxy_pass http://backend:8000;' \
      '        proxy_set_header Host $host;' \
      '        proxy_set_header X-Forwarded-Proto $scheme;' \
      '    }' \
      '    location /openapi.json {' \
      '        proxy_pass http://backend:8000;' \
      '        proxy_set_header Host $host;' \
      '    }' \
      '}' \
      > /etc/nginx/conf.d/default.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
