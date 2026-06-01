#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博 Cookie 健康检测 + 自动续期脚本
cron: 0 22 * * *
new Env('微博Cookie检测');

运行逻辑：
  1. 读取 Netscape Cookie 文件，检查 ALF/SUB 关键 token 的过期时间
  2. 若距过期 <= RENEW_AHEAD_DAYS 天（默认7天），则触发续期
  3. 若文件检测通过，再做一次轻量 API 验证，防止服务端提前吊销
  4. 续期方式：获取微博二维码 → 发送到 Telegram → 等待用户扫码 → 自动写入新 Cookie
"""

import os
import sys
import time
import requests

# 确保可以 import 同目录的 login.py / wb.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from login import (
    SESSION,
    COOKIE_FILE,
    get_qr_code,
    send_telegram_photo,
    send_telegram_message,
    poll_qr_login,
    process_login,
    save_cookie_netscape,
)

# ── 配置（可通过环境变量覆盖） ────────────────────────────────
# 距过期还有几天内触发续期（建议 5~14 天）
RENEW_AHEAD_DAYS = int(os.getenv('RENEW_AHEAD_DAYS', '7'))
# 等待用户扫码的超时秒数（默认 5 分钟）
QR_TIMEOUT = int(os.getenv('QR_TIMEOUT', '300'))


# ── 工具函数 ─────────────────────────────────────────────────

def parse_key_expiries(cookie_file):
    """
    解析 Netscape 格式 Cookie 文件，返回关键 token 的
    {name: expiry_timestamp} 字典。
    重点关注 ALF（登录凭证）、SUB（会话 token）、SUBP（扩展会话）。
    """
    key_tokens = {'ALF', 'SUB', 'SUBP'}
    expiries = {}

    if not os.path.isfile(cookie_file):
        return expiries

    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                # Netscape 格式：domain flag path secure expiry name value
                if len(parts) >= 7:
                    name = parts[5]
                    expiry_str = parts[4]
                    if name in key_tokens and expiry_str.isdigit():
                        expiry = int(expiry_str)
                        # 过期时间为 0 表示 session cookie，忽略
                        if expiry > 0:
                            expiries[name] = expiry
    except Exception as e:
        print(f"[!] 解析 Cookie 文件失败: {e}")

    return expiries


def check_expiry(expiries):
    """
    检查过期状态。
    返回 (need_renew: bool, reason: str, days_left: float | None)
    """
    if not expiries:
        return False, "未找到有效的过期时间字段，跳过过期检测", None

    now = int(time.time())

    # 取所有关键 token 中最早过期的
    earliest_name = min(expiries, key=expiries.get)
    earliest_ts = expiries[earliest_name]
    days_left = (earliest_ts - now) / 86400

    if days_left <= 0:
        return True, f"{earliest_name} 已过期 {abs(days_left):.1f} 天", days_left

    if days_left <= RENEW_AHEAD_DAYS:
        return True, f"{earliest_name} 将在 {days_left:.1f} 天后过期（阈值 {RENEW_AHEAD_DAYS} 天）", days_left

    return False, f"{earliest_name} 还剩 {days_left:.1f} 天到期", days_left


def validate_cookie_via_api(cookie_str):
    """
    通过轻量 API 实际验证 Cookie 是否被服务端认可。
    返回：
      True  — 有效
      False — 已失效（ok=-100 或重定向到登录页）
      None  — 网络异常，无法判断（不触发续期）
    """
    s = requests.Session()
    s.headers.update({
        'User-Agent': SESSION.headers.get('User-Agent', 'Mozilla/5.0'),
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://weibo.com/',
    })
    for item in cookie_str.split('; '):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            s.cookies.set(k.strip(), v.strip())

    try:
        resp = s.get(
            'https://weibo.com/ajax/profile/info',
            params={'uid': ''},
            timeout=15
        )
        data = resp.json()
        ok_code = data.get('ok', 0)
        if ok_code == -100:
            return False
        # ok=1 或其他非 -100 均视为有效
        return True
    except requests.exceptions.RequestException as e:
        print(f"[!] 网络请求失败，跳过 API 验证: {e}")
        return None
    except Exception as e:
        print(f"[!] API 验证异常: {e}")
        return None


def get_cookie_str_from_file():
    """从 Cookie 文件读取 cookie 字符串（复用 wb.py 的解析逻辑）"""
    from wb import get_cookies
    cookies = get_cookies()
    return cookies[0] if cookies else None


# ── 续期流程 ─────────────────────────────────────────────────

def do_renew(reason):
    """
    触发 Telegram 扫码续期流程。
    返回 True 表示成功，False 表示失败/超时。
    """
    print(f"[*] 开始续期流程，原因：{reason}")

    # 预检 Telegram 凭证
    from login import TG_TOKEN, TG_CHAT_ID
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[-] 未配置 TG_TOKEN / TG_CHAT_ID，无法发送二维码，续期中止")
        print("    请在 docker-compose.yml 中设置 TG_TOKEN 和 TG_CHAT_ID")
        return False

    send_telegram_message(
        f"⚠️ 微博 Cookie 需要续期！\n"
        f"📋 原因：{reason}\n\n"
        f"📲 正在生成登录二维码，请准备好微博 APP 扫码...\n"
        f"⏰ 等待时长：{QR_TIMEOUT // 60} 分钟"
    )

    try:
        # 1. 获取二维码
        qrid, qr_bytes = get_qr_code()

        # 2. 发送二维码到 Telegram
        caption = (
            "📱 请用微博 APP 扫描此二维码重新登录\n"
            "✅ 扫码并确认后 Cookie 将自动更新\n"
            f"⏰ 二维码有效期：5 分钟"
        )
        if not send_telegram_photo(qr_bytes, caption):
            print("[-] 二维码发送 Telegram 失败")
            send_telegram_message("❌ 二维码发送失败，请手动更新 Cookie 文件")
            return False

        print("[+] 二维码已发送至 Telegram，等待扫码...")

        # 3. 轮询扫码结果
        alt_url = poll_qr_login(qrid, timeout_seconds=QR_TIMEOUT)
        if not alt_url:
            print("[-] 扫码超时或二维码失效")
            send_telegram_message(
                "❌ 扫码超时，Cookie 未更新\n"
                "📅 明天 22:00 将再次自动检测并发送二维码"
            )
            return False

        # 4. 获取并保存新 Cookie
        cookie_str = process_login(alt_url)
        save_cookie_netscape(cookie_str)

        send_telegram_message(
            "✅ 微博 Cookie 已自动更新成功！\n"
            f"📅 新 Cookie 有效期约 1~3 个月\n"
            f"🕛 今晚 00:00 将使用新 Cookie 正常签到"
        )
        print("[+] Cookie 续期成功，新 Cookie 已写入文件")
        return True

    except Exception as e:
        print(f"[-] 续期过程发生异常: {e}")
        send_telegram_message(f"❌ Cookie 自动续期失败\n错误信息：{e}")
        return False


# ── 主逻辑 ───────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  微博 Cookie 健康检测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  提前续期阈值: {RENEW_AHEAD_DAYS} 天")
    print(f"  Cookie 文件: {COOKIE_FILE}")
    print("=" * 50)

    need_renew = False
    renew_reason = ""

    # ── Step 1: 解析 Netscape 文件，检查过期时间 ──────────────
    expiries = parse_key_expiries(COOKIE_FILE)
    if expiries:
        token_info = ', '.join(
            f"{k}={time.strftime('%Y-%m-%d', time.localtime(v))}"
            for k, v in sorted(expiries.items(), key=lambda x: x[1])
        )
        print(f"[*] 关键 Token 过期日期: {token_info}")

        need_renew, reason_msg, days_left = check_expiry(expiries)
        print(f"[{'!' if need_renew else '+'}] {reason_msg}")

        if need_renew:
            renew_reason = reason_msg
    else:
        print("[!] 未在 Cookie 文件中找到有效的过期时间字段，将跳过时间检测")

    # ── Step 2: 若时间检测通过，再做 API 实时验证 ───────────────
    if not need_renew:
        print("[*] 正在通过 API 验证 Cookie 有效性...")
        cookie_str = get_cookie_str_from_file()

        if cookie_str:
            valid = validate_cookie_via_api(cookie_str)
            if valid is True:
                print("[+] API 验证通过，Cookie 有效")
            elif valid is False:
                need_renew = True
                renew_reason = "API 验证失败，Cookie 已被服务端吊销"
                print(f"[!] {renew_reason}")
            else:
                print("[!] 网络异常，跳过 API 验证，不触发续期")
        else:
            print("[!] 未能读取 Cookie 内容，跳过 API 验证")

    # ── Step 3: 触发续期或正常退出 ───────────────────────────────
    if need_renew:
        success = do_renew(renew_reason)
        sys.exit(0 if success else 1)
    else:
        print("[+] Cookie 状态良好，无需续期 ✅")
        sys.exit(0)


if __name__ == '__main__':
    main()
