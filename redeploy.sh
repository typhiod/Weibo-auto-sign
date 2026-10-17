#!/bin/bash
# 在已经部署的项目目录执行；沿用原 compose 配置及 data/logs 挂载。
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f docker-compose.yml ] && [ ! -f docker-compose.yaml ] && [ ! -f compose.yml ] && [ ! -f compose.yaml ]; then
    echo "[!] 未找到现有 compose 配置。请把更新包解压到原部署目录。"
    echo "    直接使用 Python/crontab 的部署，请运行 bash deploy.sh 更新定时任务。"
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo "[!] 未找到 Docker Compose，请在原部署主机上运行此脚本。"
    exit 1
fi

echo "[*] 检查现有 compose 配置..."
"${COMPOSE_CMD[@]}" config --quiet
echo "[*] 重新构建并启动 weibo-sign，沿用现有环境变量及挂载目录..."
"${COMPOSE_CMD[@]}" up -d --build --force-recreate weibo-sign
"${COMPOSE_CMD[@]}" ps weibo-sign
echo "[+] 更新完成。查看保活状态可执行："
echo "    ${COMPOSE_CMD[*]} exec -T weibo-sign python -u heartbeat.py"
echo "    ${COMPOSE_CMD[*]} exec -T weibo-sign tail -n 30 /app/logs/heartbeat.log"
