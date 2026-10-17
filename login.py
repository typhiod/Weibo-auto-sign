#!/usr/bin/env python3
"""
微博扫码登录 - 获取并保存服务端实际签发的 Cookie
通过Telegram接收二维码，手机微博扫码即可登录
有效期由微博服务端决定
"""
import os
import sys
import json
import time
import requests
import re
from cookie_store import (
    TaskBusy, cookie_header, cookie_value, default_cookie_file, load_accounts,
    make_session, parse_cookie_content, save_cookie_jar, task_lock, validate_session,
)

TG_TOKEN = os.getenv('TG_TOKEN', '')
TG_CHAT_ID = os.getenv('TG_CHAT_ID', '')
COOKIE_FILE = str(default_cookie_file())
SESSION = make_session('')


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
    try:
        response = requests.post(url, json={'chat_id': TG_CHAT_ID, 'text': text}, timeout=15)
        response.raise_for_status()
        return response.json().get('ok', False)
    except (requests.exceptions.RequestException, ValueError) as error:
        print(f'[!] Telegram 文本通知发送失败 ({type(error).__name__})')
        return False


def get_qr_code():
    """获取微博登录二维码"""
    # 每次扫码使用干净的会话，避免混入上一个账户的凭证。
    SESSION.cookies.clear()
    # Step 1: 获取二维码参数
    ts = int(time.time() * 10000)
    url = f'https://login.sina.com.cn/sso/qrcode/image?entry=miniblog&size=180&callback=STK_{ts}'
    resp = SESSION.get(url, timeout=15)
    resp.raise_for_status()
    
    # 解析 JSONP (e.g. STK_123({...}))
    match = re.search(r'\((.*)\)', resp.text)
    if not match:
        raise Exception("获取二维码解析失败，响应不是预期的 JSONP")
        
    data = json.loads(match.group(1))
    if data.get('retcode') != 20000000:
        raise Exception(f"获取二维码失败 (retcode={data.get('retcode')})")

    qrid = data['data']['qrid']
    # Sina 新接口返回的是图片服务参数，需自己拼接
    if 'image' in data['data'] and 'api_key=' in data['data']['image']:
        image_url = data['data']['image']
        if image_url.startswith('//'):
            image_url = 'https:' + image_url
    else:
        # 兼容备用
        api_key = re.search('.*?"api_key":"(.*)?",', resp.text)
        if api_key:
            image_url = f"https://v2.qr.weibo.cn/inf/gen?api_key={api_key.group(1)}"
        else:
            raise Exception("未能从响应中提取 api_key 或 image_url")

    # Step 2: 下载二维码图片
    img_resp = SESSION.get(image_url, timeout=15)
    img_resp.raise_for_status()

    print(f"[+] 获取到二维码, qrid={qrid}")
    return qrid, img_resp.content


def poll_qr_login(qrid, timeout_seconds=300):
    """轮询扫码结果"""
    start = time.time()
    while time.time() - start < timeout_seconds:
        ts = int(time.time() * 10000)
        try:
            resp = SESSION.get(
                f'https://login.sina.com.cn/sso/qrcode/check?qrid={qrid}&entry=miniblog&callback=STK_{ts}',
                timeout=15
            )
            match = re.search(r'\((.*)\)', resp.text)
            data = json.loads(match.group(1)) if match else None
        except (requests.exceptions.RequestException, ValueError) as error:
            # 单次网络抖动不能让用户已经扫过的二维码作废
            print(f'\n[-] 轮询请求失败 ({type(error).__name__})，稍后重试')
            data = None
        if not isinstance(data, dict):
            time.sleep(2)
            continue
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


def process_login(alt_token):
    """处理登录重定向，提取Cookie"""
    # 1. 构造获取跨域登录链接的 API
    ts = int(time.time() * 10000)
    alturl = (f'https://login.sina.com.cn/sso/login.php?entry=miniblog&returntype=TEXT'
              f'&crossdomain=1&cdult=3&domain=weibo.com&alt={alt_token}&savestate=30'
              f'&callback=STK_{ts}')
    
    print("[*] 正在换取跨域凭证...")
    resp = SESSION.get(alturl, timeout=30)
    resp.raise_for_status()
    
    match = re.search(r'\((.*)\)', resp.text)
    if not match:
        raise Exception("跨域票据获取失败，响应不是预期的 JSONP")
        
    data = json.loads(match.group(1))
    crossDomainUrlList = data.get('crossDomainUrlList', [])
    
    if not crossDomainUrlList:
        raise Exception("未能获取到跨域登录链接")
        
    # 2. 依次请求跨域链接来写入最终 Cookie
    print(f"[*] 获取到 {len(crossDomainUrlList)} 个跨域验证链接，正在写入Cookie...")
    for url in crossDomainUrlList:
        req_url = url + '&action=login' if 'action=login' not in url else url
        SESSION.get(req_url, timeout=15).raise_for_status()

    # 3. 最终从 session 提取 weibo.com cookie
    if not cookie_value(SESSION.cookies, 'SUB'):
        raise Exception('未获取到 weibo.com 的 SUB 登录凭证，保留原 Cookie')
    valid, reason = validate_session(SESSION)
    if valid is not True:
        raise Exception(f'扫码后的登录验证未通过: {reason}；保留原 Cookie')
    print(f'[+] 登录验证通过，获取到 {len(SESSION.cookies)} 个 Cookie 字段')
    return cookie_header(SESSION.cookies)


def save_cookie_netscape(cookie_str=None, cookie_file=None):
    """扫码流程保存完整 CookieJar；兼容字符串调用时不伪造过期时间。"""
    jar = SESSION.cookies if cookie_str is None else parse_cookie_content(cookie_str)
    target = cookie_file or COOKIE_FILE
    save_cookie_jar(jar, target)
    print('[+] Cookie 已保存，保留真实域名、路径和过期时间')


def main(account_index=1):
    print("=" * 50)
    print("  微博扫码登录工具")
    print("=" * 50)

    if not TG_TOKEN or not TG_CHAT_ID:
        print("[-] 未配置Telegram，无法发送二维码")
        print("    请设置环境变量: TG_TOKEN, TG_CHAT_ID")
        sys.exit(1)

    try:
        accounts = load_accounts()
        if accounts and not 1 <= account_index <= len(accounts):
            raise ValueError('账户编号超出范围')
        account = accounts[account_index - 1] if accounts else None
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
        process_login(alt_url)

        # 5. 保存
        save_cookie_netscape(cookie_file=account.path if account else COOKIE_FILE)

        send_telegram_message(f'✅ 微博账户{account_index}登录成功!\nCookie字段数: {len(SESSION.cookies)}\n已保留服务端实际过期时间，有效期以微博登录状态为准')

    except Exception as e:
        print(f"[-] 登录失败 ({type(e).__name__})")
        send_telegram_message(f'❌ 微博登录失败 ({type(e).__name__})，原 Cookie 已保留，请检查登录日志')
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='微博扫码登录')
    parser.add_argument('--account', type=int, default=1, help='需要更新的账户编号，默认 1')
    args = parser.parse_args()
    try:
        with task_lock():
            main(args.account)
    except TaskBusy as error:
        print(f'[*] {error}')
