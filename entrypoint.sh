#!/bin/bash
set -euo pipefail

TZ="${TZ:-Asia/Shanghai}"
SIGN_CRON="${SIGN_CRON:-0 0 * * *}"
HEARTBEAT_CRON="${HEARTBEAT_CRON:-0 */6 * * *}"
CHECK_COOKIE_CRON="${CHECK_COOKIE_CRON:-0 22 * * *}"
PYTHON_BIN="${PYTHON_BIN:-/usr/local/bin/python3}"
RUN_STARTUP_CATCHUP="${RUN_STARTUP_CATCHUP:-1}"
STARTUP_CATCHUP_WINDOW_MINUTES="${STARTUP_CATCHUP_WINDOW_MINUTES:-360}"
CRON_FILE="/etc/cron.d/weibo-sign"
CRON_ENV="/app/.cron_env"
STARTUP_SIGN_STAMP="/app/logs/.last_startup_sign_date"

echo "========================================"
echo "  微博超话签到 Docker 容器"
echo "  时区: $TZ"
echo "  Cookie检测: 每天 22:00"
echo "  签到: 每天 00:00"
echo "  保活: 每 6 小时"
echo "========================================"

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "[!] 未找到可用的 python3 可执行文件"
    exit 1
fi

mkdir -p /app/logs /app/data
touch /app/logs/sign.log /app/logs/heartbeat.log /app/logs/check_cookie.log

# 检查Cookie文件
if [ -f "/app/data/weibo.com_cookies.txt" ]; then
    echo "[+] Cookie文件已挂载: data/weibo.com_cookies.txt"
elif [ -f "/app/weibo.com_cookies.txt" ]; then
    echo "[+] Cookie文件已挂载: weibo.com_cookies.txt (旧路径)"
elif [ -f "/app/data/cookie.txt" ]; then
    echo "[+] Cookie文件已挂载: data/cookie.txt"
else
    echo "[!] 未检测到Cookie文件，请放置到 ./data/weibo.com_cookies.txt"
fi

# 设置通知环境变量
if [ -n "${SC_KEY:-}" ]; then
    echo "[+] Server酱通知已配置"
fi
if [ -n "${SMTP_HOST:-}" ]; then
    echo "[+] SMTP邮件通知已配置"
fi

: > "$CRON_ENV"
for var_name in TZ NOTIFY_TYPE TG_TOKEN TG_CHAT_ID SC_KEY PUSH_KEY SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASS SMTP_TO WEIBO_COOKIE WEIBO_COOKIES RUN_STARTUP_CATCHUP STARTUP_CATCHUP_WINDOW_MINUTES RENEW_AHEAD_DAYS QR_TIMEOUT; do
    if [ -n "${!var_name:-}" ]; then
        printf 'export %s=%q\n' "$var_name" "${!var_name}" >> "$CRON_ENV"
    fi
done
chmod 600 "$CRON_ENV"

cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
TZ=$TZ
CRON_TZ=$TZ

$CHECK_COOKIE_CRON root source /app/.cron_env >/dev/null 2>&1; cd /app && $PYTHON_BIN -u check_cookie.py >> /app/logs/check_cookie.log 2>&1
$SIGN_CRON root source /app/.cron_env >/dev/null 2>&1; cd /app && $PYTHON_BIN -u wb.py >> /app/logs/sign.log 2>&1
$HEARTBEAT_CRON root source /app/.cron_env >/dev/null 2>&1; cd /app && $PYTHON_BIN -u heartbeat.py >> /app/logs/heartbeat.log 2>&1
EOF
chmod 0644 "$CRON_FILE"

echo "[+] Python: $PYTHON_BIN"
echo "[+] 当前容器时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "[+] 已写入 cron 任务:"
echo "    $CHECK_COOKIE_CRON -> check_cookie.py"
echo "    $SIGN_CRON -> wb.py"
echo "    $HEARTBEAT_CRON -> heartbeat.py"

run_startup_catchup() {
    if [ "$RUN_STARTUP_CATCHUP" != "1" ]; then
        return
    fi

    local today last_run current_hour current_minute current_total_minutes
    today="$(date +%F)"
    last_run="$(cat "$STARTUP_SIGN_STAMP" 2>/dev/null || true)"

    if [ "$last_run" = "$today" ]; then
        echo "[*] 今日已经执行过启动补签，跳过"
        return
    fi

    current_hour="$(date +%H)"
    current_minute="$(date +%M)"
    current_total_minutes=$((10#$current_hour * 60 + 10#$current_minute))

    if [ "$current_total_minutes" -gt "$STARTUP_CATCHUP_WINDOW_MINUTES" ]; then
        echo "[*] 当前时间已超过启动补签窗口，跳过补签"
        return
    fi

    echo "[*] 当前时间位于启动补签窗口内，先执行一次补签..."
    if cd /app && "$PYTHON_BIN" -u wb.py >> /app/logs/sign.log 2>&1; then
        echo "$today" > "$STARTUP_SIGN_STAMP"
        echo "[+] 启动补签完成"
    else
        echo "[!] 启动补签失败，请检查 /app/logs/sign.log"
    fi
}

run_startup_catchup

echo "[*] 启动 cron 守护进程..."
echo ""

# 启动cron前台运行
exec cron -f
