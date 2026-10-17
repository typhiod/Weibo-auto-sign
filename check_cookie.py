#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微博 Cookie 健康检测 + 自动续期脚本
cron: 0 22 * * *
new Env('微博Cookie检测');

运行逻辑：
  1. 实际验证每个账户的登录状态，接收并保存服务器更新的 Cookie
  2. 验证有效后检查 ALF/SUB/SUBP 的真实过期时间
  3. 明确失效或验证后仍临近到期才触发扫码；网络异常不触发扫码
  4. 续期方式：获取微博二维码 → 发送到 Telegram → 等待用户扫码 → 自动写入新 Cookie
"""

import os
import sys
import time
from cookie_store import (
    TaskBusy, key_expiries, load_accounts, make_session, read_cookie_file,
    task_lock, validate_session,
)

# 确保可以 import 同目录的 login.py / wb.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from login import (
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
    """只读取微博根域登录凭证的真实过期时间。"""
    if not os.path.isfile(cookie_file):
        return {}
    return key_expiries(read_cookie_file(cookie_file))


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
    """兼容旧调用：True 有效、False 明确失效、None 无法确认。"""
    with make_session(cookie_str) as session:
        valid, reason = validate_session(session)
        print(f"[*] {reason}")
        return valid


# ── 续期流程 ─────────────────────────────────────────────────

def do_renew(reason, trigger_sign=True, account=None):
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

    try:
        send_telegram_message(
            f"⚠️ 微博 Cookie 需要续期！\n"
            f"📋 原因：{reason}\n\n"
            f"📲 正在生成登录二维码，请准备好微博 APP 扫码...\n"
            f"⏰ 等待时长：{QR_TIMEOUT // 60} 分钟"
        )
        # 1. 获取二维码
        qrid, qr_bytes = get_qr_code()

        # 2. 发送二维码到 Telegram
        caption = (
            "📱 请用微博 APP 扫描此二维码重新登录\n"
            "✅ 扫码并确认后 Cookie 将自动更新并立即执行签到\n"
            f"⏰ 等待扫码：{QR_TIMEOUT} 秒\n"
            f"👤 {account.source if account else '默认账户'}"
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
                "📅 下次 Cookie 检测任务将再次检查"
            )
            return False

        # 4. 获取并保存新 Cookie
        process_login(alt_url)
        save_cookie_netscape(cookie_file=account.path if account else COOKIE_FILE)
        if account:
            account.reload()

        send_telegram_message(
            "✅ 微博 Cookie 已自动更新成功！\n"
            "📅 已保留服务端实际过期时间\n"
            + ("🚀 正在执行超话签到..." if trigger_sign else "🚀 将重试当前账户的超话签到")
        )
        print("[+] Cookie 续期成功，新 Cookie 已写入文件")

        # 5. 自动执行一次签到，避免当天漏签
        if trigger_sign:
            try:
                print("[*] 正在执行自动签到任务...")
                from wb import main as run_wb_main
                run_wb_main(accounts=[account] if account else None, allow_renew=False)
            except SystemExit:
                pass
            except Exception as e:
                print(f"[!] 自动签到触发异常 ({type(e).__name__})")

        return True

    except Exception as e:
        print(f"[-] 续期过程发生异常 ({type(e).__name__})")
        try:
            send_telegram_message(f"❌ Cookie 自动续期失败\n错误类型：{type(e).__name__}")
        except Exception:
            print('[!] 续期失败通知未能发送')
        return False


# ── 主逻辑 ───────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  微博 Cookie 健康检测")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  提前续期阈值: {RENEW_AHEAD_DAYS} 天")
    print("=" * 50)
    accounts = load_accounts()
    if not accounts:
        print('[!] 未找到 Cookie，尝试首次扫码登录')
        return 0 if do_renew('未找到 Cookie') else 1

    failures = 0
    for account in accounts:
        print(f'[*] 检测 {account.source}')
        try:
            # 先尝试实际访问并接收刷新，避免根据旧文件过早要求扫码。
            with make_session(account.jar) as session:
                valid, reason = validate_session(session)
                if valid is None:
                    print(f'[!] 无法确认登录状态: {reason}；保留原 Cookie，不触发扫码')
                    failures += 1
                    continue
                if valid is True:
                    account.persist(session.cookies)
                    need_renew, expiry_reason, _ = check_expiry(key_expiries(session.cookies))
                    print(f'[+] 登录有效，Cookie 已保存；{expiry_reason}')
                    if not need_renew:
                        continue
                    reason = expiry_reason
                else:
                    print(f'[!] 登录已失效: {reason}')
            if not do_renew(f'{account.source}: {reason}', account=account):
                failures += 1
        except (OSError, ValueError) as error:
            print(f'[!] Cookie 读取/保存失败 ({type(error).__name__})，不触发扫码')
            failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        with task_lock():
            sys.exit(main())
    except TaskBusy as error:
        print(f'[*] {error}')
