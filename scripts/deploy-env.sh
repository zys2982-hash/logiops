#!/usr/bin/env bash
# 生成部署用的根目录 .env（随机 SECRET_KEY + 强口令），**在云服务器上跑**。
#
# 用法：
#   bash scripts/deploy-env.sh http://<公网IP>      # 最后一步会在控制台打印口令，抄走存好
#   bash scripts/deploy-env.sh https://demo.example.com
#
# 说明：
# - 幂等保护：已存在 .env 就直接退出（不会覆盖你的密钥）
# - 生成的 .env 权限 600，且 .gitignore 已忽略 .env，不会进仓库
# - 演示账号口令（DEMO_PASSWORD）会被 seed 用来建号；**改了它必须重新 seed**：
#     docker compose exec backend python -m app.seed --reset --demo
set -euo pipefail

cd "$(dirname "$0")/.."

ORIGIN="${1:-}"
if [ -z "$ORIGIN" ]; then
  echo "用法：bash scripts/deploy-env.sh http://<公网IP 或域名>"
  exit 2
fi

if [ -f .env ]; then
  echo "⚠️  .env 已存在，未改动。要重新生成先备份：mv .env .env.bak"
  exit 1
fi

gen() { openssl rand -base64 24 | tr -d '/+=' | cut -c1-"$1"; }

SECRET_KEY="$(openssl rand -hex 32)"
MYSQL_ROOT_PASSWORD="$(gen 20)"
MYSQL_PASSWORD="$(gen 20)"
DEMO_PASSWORD="$(gen 16)"

cat > .env <<EOF
# 由 scripts/deploy-env.sh 生成（$(date '+%Y-%m-%d %H:%M:%S')）—— 别提交进仓库
APP_ENV=local
SECRET_KEY=$SECRET_KEY
CORS_ORIGINS=$ORIGIN
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
MYSQL_PASSWORD=$MYSQL_PASSWORD
DEMO_PASSWORD=$DEMO_PASSWORD
WEB_PORT=80
SEED_SCALE=compact
LLM_API_KEY=
EOF
chmod 600 .env

cat <<EOF

✅ 已生成 .env（权限 600）—— 下面这些**只显示这一次，抄走存好**：

   演示账号：owner@logiops.dev / admin@logiops.dev / operator@logiops.dev / viewer@logiops.dev
   演示口令：$DEMO_PASSWORD
   MySQL   ：用户 logiops  口令 $MYSQL_PASSWORD   （root 口令 $MYSQL_ROOT_PASSWORD）

下一步：
   docker compose up -d --build
   docker compose exec backend python -m app.seed --reset --demo     # 用上面的口令建号
   浏览器打开 $ORIGIN ，用 owner@logiops.dev + 上面的口令登录

改口令：编辑 .env 里的 DEMO_PASSWORD，重新执行最后那条 seed 命令即可。
EOF
