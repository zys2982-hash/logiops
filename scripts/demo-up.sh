#!/usr/bin/env bash
# LogiOps 启动自检：关机再开机后（或面试前）跑这一条就够了。
#
# 用法（在仓库根目录）：
#   bash scripts/demo-up.sh
#
# 它会：拉起容器 → 等后端就绪（最多 90 秒）→ 自检 80 端口转发（必要时自动重启 frontend
# 修掉 nginx 缓存后端旧 IP 的 502）→ 打印容器状态与访问地址。
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1/5 启动容器（已运行的不会被重启）=="
docker compose up -d

echo "== 2/5 等后端就绪（最多 90 秒）=="
ready=0
for _ in $(seq 1 45); do
  if curl -fsS -m 2 http://127.0.0.1:8000/api/v1/healthz >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" != "1" ]; then
  echo "  ✗ 90 秒仍未就绪，后端日志末尾："
  docker compose logs --tail=50 backend
  exit 1
fi
echo "  ✓ 后端已就绪"

echo "== 3/5 检查 80 端口转发（nginx → backend）=="
if ! curl -fsS -m 5 http://127.0.0.1/api/v1/healthz >/dev/null 2>&1; then
  echo "  ! 转发失败（多半是 nginx 缓存了后端的旧 IP）→ 重启 frontend"
  docker compose restart frontend >/dev/null
  sleep 3
fi
if curl -fsS -m 5 http://127.0.0.1/api/v1/healthz >/dev/null 2>&1; then
  echo "  ✓ 80 端口正常"
else
  echo "  ✗ 80 端口仍失败，看：docker compose logs --tail=50 frontend"
  exit 1
fi

echo "== 4/5 容器状态 =="
docker compose ps

echo "== 5/5 访问地址 =="
port="$(grep -E '^WEB_PORT=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)"
port="${port:-80}"
ip="$(curl -s -m 5 https://myip.ipip.net 2>/dev/null | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1 || true)"
echo "  本机：http://127.0.0.1:${port}"
if [ -n "$ip" ]; then
  echo "  公网：http://${ip}:${port}    ← 对外演示用这个（关机再开 IP 可能变，以这里打印的为准）"
fi
echo
echo "重建演示数据（清空后重新 seed）："
echo "  docker compose exec backend python -m app.seed --reset --demo"
