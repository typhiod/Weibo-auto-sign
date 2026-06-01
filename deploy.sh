#!/bin/bash
# -*- coding: utf-8 -*-
#
# 微博超话签到 - Linux 一键部署脚本
# 功能: 安装依赖、设置每天12:00自动签到、Cookie保活
#

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="wb.py"
CRON_MARKER="# weibo-chaohua-sign"

echo "========================================"
echo "  微博超话签到 - Linux 部署脚本"
echo "========================================"

# 1. 检测架构
ARCH=$(uname -m)
echo "[*] 检测到架构: $ARCH"
case "$ARCH" in
    aarch64|armv7l|armv8l)
        echo "[+] ARM架构 OK"
        ;;
    *)
        echo "[!] 非ARM架构 ($ARCH)，继续..."
        ;;
esac

# 2. 检查Python3
if ! command -v python3 &>/dev/null; then
    echo "[*] 安装 Python3..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip
fi
echo "[+] Python3: $(python3 --version)"

# 3. 安装依赖
echo "[*] 安装 Python 依赖..."
python3 -m pip install --user requests 2>/dev/null || pip3 install requests 2>/dev/null || sudo pip3 install requests
echo "[+] requests 已安装"

# 4. 检查Cookie文件
COOKIE_FILE=""
for f in "weibo.com_cookies.txt" "cookie.txt" "cookies.txt"; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        COOKIE_FILE="$f"
        echo "[+] 检测到Cookie文件: $f"
        break
    fi
done

if [ -z "$COOKIE_FILE" ]; then
    echo ""
    echo "  [!] 未找到Cookie文件！"
    echo "  [>] 请将浏览器导出的 weibo.com_cookies.txt 放到:"
    echo "      $SCRIPT_DIR/"
    echo ""
fi

# 5. 设置定时任务
echo ""
echo "[*] 配置 crontab 定时任务..."

# 生成crontab条目
CRON_SIGN="0 12 * * * cd $SCRIPT_DIR && python3 $SCRIPT_NAME >> $SCRIPT_DIR/sign.log 2>&1"
CRON_HEARTBEAT="0 */6 * * * cd $SCRIPT_DIR && python3 -c \"
import requests, os, sys
sys.path.insert(0, '$SCRIPT_DIR')
from wb import get_cookies
cookies = get_cookies()
if cookies:
    s = requests.Session()
    s.cookies.update({k:v for k,v in [c.split('=',1) for c in cookies[0].split('; ') if '=' in c]})
    s.get('https://weibo.com', timeout=15)
    print(f'Heartbeat OK: {len(cookies[0])} bytes cookie')
\" >> $SCRIPT_DIR/heartbeat.log 2>&1"

# 移除旧的任务
(crontab -l 2>/dev/null | grep -v "$CRON_MARKER") | crontab - || true

# 添加新任务
(
    crontab -l 2>/dev/null || true
    echo ""
    echo "$CRON_MARKER-begin"
    echo "$CRON_SIGN $CRON_MARKER"
    echo "$CRON_HEARTBEAT $CRON_MARKER"
    echo "$CRON_MARKER-end"
) | crontab -

echo "[+] 已添加定时任务:"
echo "    签到: 每天 12:00 (北京时间)"
echo "    保活: 每 6 小时访问微博首页"

# 6. 通知配置提示
echo ""
echo "========================================"
echo "  通知配置 (可选)"
echo "========================================"
echo ""
echo "  Server酱 (推荐，免费):"
echo "    1. 访问 https://sct.ftqq.com/ 扫码登录"
echo "    2. 获取 SendKey"
echo "    3. 设置环境变量:"
echo "       export NOTIFY_TYPE=serverchan"
echo "       export SC_KEY=你的SendKey"
echo ""
echo "  邮箱通知:"
echo "       export NOTIFY_TYPE=smtp"
echo "       export SMTP_HOST=smtp.qq.com"
echo "       export SMTP_PORT=465"
echo "       export SMTP_USER=你的邮箱"
echo "       export SMTP_PASS=授权码"
echo "       export SMTP_TO=接收邮箱"
echo ""
echo "  添加以上 export 到 ~/.bashrc 或 /etc/environment"
echo ""

# 7. 测试运行
echo "========================================"
echo "  测试运行"
echo "========================================"
echo ""
read -p "  是否立即测试签到? (y/n): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cd "$SCRIPT_DIR"
    python3 "$SCRIPT_NAME"
    echo ""
    echo "[+] 测试完成，查看日志: tail -f $SCRIPT_DIR/sign.log"
else
    echo "[*] 跳过测试。手动运行: cd $SCRIPT_DIR && python3 $SCRIPT_NAME"
fi

echo ""
echo "========================================"
echo "  部署完成！"
echo "========================================"
echo ""
echo "  日志文件:"
echo "    签到日志: $SCRIPT_DIR/sign.log"
echo "    保活日志: $SCRIPT_DIR/heartbeat.log"
echo ""
echo "  常用命令:"
echo "    crontab -l              # 查看定时任务"
echo "    tail -f sign.log        # 查看签到日志"
echo "    python3 wb.py           # 手动签到"
echo ""
