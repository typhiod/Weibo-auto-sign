#!/usr/bin/env python3
"""
微博扫码登录 - 获取长期有效Cookie
通过Telegram接收二维码，手机微博扫码即可登录
Cookie有效期通常1-3个月
"""
import os
import sys
import json
import time
import requests
import base64
import re
from io import BytesIO

TG_TOKEN = os.getenv('TG_TOKEN', '')
TG_CHAT_ID = os.getenv('TG_CHAT_ID', '')
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'weibo.com_cookies.txt')
# 兼容旧路径：如果 data/ 目录不存在则回退到脚本同目录
if not os.path.isdir(os.path.dirname(COOKIE_FILE)):
    COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'weibo.com_cookies.txt')

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    'Referer': 'https://weibo.com/',
})


def send_telegram_photo(image_bytes, caption=''):
    """发送图片到Telegram"""
    url = f'https://api.telegram.org/bot{TG_TOKEN}/sendPhoto'
    files = {'photo': ('qr.png', image_bytes, 'image/png')}
    data = {'chat_id': TG_CHAT_ID, 'caption': caption}
    resp = requests.post(url, files=files, data=data, timeout=30)
    return resp.json().get('ok', False)


def send_telegram_message(text):
    """发送文本到Telegram"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print(text)
        return
    url = f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage'
    requests.post(url, json={'chat_id': TG_CHAT_ID, 'text': text}, timeout=15)


def get_qr_code():
    """获取微博登录二维码"""
    # Step 1: 获取二维码参数
    ts = int(time.time() * 1000)
    resp = SESSION.get(f'https://login.sina.com.cn/sso/qrcode/image?entry=weibo&size=160&_={ts}', timeout=15)
    data = resp.json()
    if data.get('retcode') != 20000000:
        raise Exception(f"获取二维码失败: {data}")

    qrid = data['data']['qrid']
    image_url = f"https:{data['data']['image']}"

    # Step 2: 下载二维码图片
    img_resp = SESSION.get(image_url, timeout=15)

    print(f"[+] 获取到二维码, qrid={qrid}")
    return qrid, img_resp.content


def poll_qr_login(qrid, timeout_seconds=300):
    """轮询扫码结果"""
    start = time.time()
    while time.time() - start < timeout_seconds:
        ts = int(time.time() * 1000)
        resp = SESSION.get(
            f'https://login.sina.com.cn/sso/qrcode/check?qrid={qrid}&entry=weibo&_={ts}',
            timeout=15
        )
        data = resp.json()
        retcode = data.get('retcode', 0)

        if retcode == 20000000:
            # 扫码成功，获取alt重定向URL
            alt_url = data['data']['alt']
            print(f"[+] 扫码成功，正在获取登录信息...")
            return alt_url
        elif retcode == 50114001:
            elapsed = int(time.time() - start)
            print(f"\r[*] 等待扫码... {elapsed}s / {timeout_seconds}s", end='', flush=True)
        elif retcode == 50114002:
            print(f"\n[*] 已扫码，等待确认...")
        elif retcode == 50114004:
            print(f"\n[-] 二维码已过期")
            return None
        else:
            print(f"\n[*] 状态: {data.get('msg', retcode)}")

        time.sleep(2)

    print(f"\n[-] 扫码超时")
    return None


def process_login(alt_url):
    """处理登录重定向，提取Cookie"""
    resp = SESSION.get(alt_url, allow_redirects=True, timeout=30)

    # 最终可能重定向到weibo.com，从cookie jar提取
    cookies = []
    for cookie in SESSION.cookies:
        cookies.append(f"{cookie.name}={cookie.value}")

    if not cookies:
        raise Exception("未能获取到登录Cookie")

    # 检查关键字段
    cookie_str = '; '.join(cookies)
    has_sub = 'SUB=' in cookie_str
    has_s = 'SUBP=' in cookie_str or 'ALF=' in cookie_str

    print(f"[+] 获取到 {len(cookies)} 个Cookie字段")
    print(f"[+] SUB: {'有' if has_sub else '无'}")
    print(f"[+] 长期凭证: {'有' if has_s else '无'}")

    return cookie_str


def save_cookie_netscape(cookie_str):
    """保存为Netscape格式（兼容现有脚本）"""
    cookies = {}
    for item in cookie_str.split('; '):
        if '=' in item:
            k, v = item.split('=', 1)
            cookies[k] = v

    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# This is a generated file! Do not edit.",
        ""
    ]

    # 模拟过期时间：能设的就设远一点（一年后）
    far_expiry = int(time.time()) + 365 * 24 * 3600

    for name, value in cookies.items():
        # domain flag path secure expiry name value
        lines.append(f".weibo.com\tTRUE\t/\tTRUE\t{far_expiry}\t{name}\t{value}")

    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

    print(f"[+] Cookie已保存到 {COOKIE_FILE}")


def main():
    print("=" * 50)
    print("  微博扫码登录工具")
    print("=" * 50)

    if not TG_TOKEN or not TG_CHAT_ID:
        print("[-] 未配置Telegram，无法发送二维码")
        print("    请设置环境变量: TG_TOKEN, TG_CHAT_ID")
        sys.exit(1)

    try:
        # 1. 获取二维码
        qrid, qr_bytes = get_qr_code()

        # 2. 发送到Telegram
        print("[*] 发送二维码到Telegram...")
        if not send_telegram_photo(qr_bytes, '📱 请用微博APP扫码登录\n⏰ 有效期: 5分钟'):
            print("[-] 发送二维码失败")
            sys.exit(1)
        print("[+] 二维码已发送，请去Telegram查看")

        # 3. 轮询结果
        alt_url = poll_qr_login(qrid, timeout_seconds=300)
        if not alt_url:
            send_telegram_message('❌ 微博扫码登录超时，请重试')
            sys.exit(1)

        # 4. 获取Cookie
        cookie_str = process_login(alt_url)

        # 5. 保存
        save_cookie_netscape(cookie_str)

        send_telegram_message(f'✅ 微博登录成功!\nCookie字段数: {len(cookie_str.split("; "))}\n有效期: 约1-3个月')

    except Exception as e:
        print(f"[-] 登录失败: {e}")
        send_telegram_message(f'❌ 微博登录失败: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
