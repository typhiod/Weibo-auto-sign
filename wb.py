#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
微博超话批量签到脚本
版本: v1.2
cron: 0 8 * * *
new Env('微博超话签到');

支持多账户配置：
1. 单账户：WEIBO_COOKIE="cookie内容"
2. 多账户：WEIBO_COOKIES="cookie1@cookie2@cookie3" 或换行分割
"""

import os
import sys
import json
import time
import random
import requests

# Windows控制台UTF-8支持
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def send_notify(title, content, notify_type=None):
    """发送通知，支持Server酱和SMTP邮箱"""
    notify_type = notify_type or os.getenv('NOTIFY_TYPE', '')

    if notify_type == 'serverchan':
        sc_key = os.getenv('SC_KEY', '') or os.getenv('PUSH_KEY', '')
        if sc_key:
            try:
                resp = requests.post(
                    f'https://sctapi.ftqq.com/{sc_key}.send',
                    json={'title': title, 'desp': content},
                    timeout=15
                )
                result = resp.json()
                if result.get('code') == 0:
                    print(f"📧 通知已发送 (Server酱)")
                else:
                    print(f"⚠️ 通知发送失败: {result.get('message', '')}")
            except Exception as e:
                print(f"⚠️ 通知发送异常: {e}")

    elif notify_type == 'smtp':
        smtp_host = os.getenv('SMTP_HOST', '')
        smtp_port = os.getenv('SMTP_PORT', '465')
        smtp_user = os.getenv('SMTP_USER', '')
        smtp_pass = os.getenv('SMTP_PASS', '')
        smtp_to = os.getenv('SMTP_TO', '')
        if smtp_host and smtp_user and smtp_to:
            try:
                import smtplib
                from email.mime.text import MIMEText
                msg = MIMEText(content, 'plain', 'utf-8')
                msg['Subject'] = title
                msg['From'] = smtp_user
                msg['To'] = smtp_to
                with smtplib.SMTP_SSL(smtp_host, int(smtp_port)) as s:
                    s.login(smtp_user, smtp_pass)
                    s.sendmail(smtp_user, [smtp_to], msg.as_string())
                print(f"📧 邮件已发送至 {smtp_to}")
            except Exception as e:
                print(f"⚠️ 邮件发送异常: {e}")

    elif notify_type == 'telegram':
        tg_token = os.getenv('TG_TOKEN', '')
        tg_chat_id = os.getenv('TG_CHAT_ID', '')
        if tg_token and tg_chat_id:
            try:
                text = f"*{title}*\n\n{content}"
                resp = requests.post(
                    f'https://api.telegram.org/bot{tg_token}/sendMessage',
                    json={'chat_id': tg_chat_id, 'text': text, 'parse_mode': 'Markdown'},
                    timeout=15
                )
                result = resp.json()
                if result.get('ok'):
                    print(f"📧 通知已发送 (Telegram)")
                else:
                    print(f"⚠️ Telegram通知失败: {result.get('description', '')}")
            except Exception as e:
                print(f"⚠️ Telegram通知异常: {e}")

    elif notify_type:
        print(f"⚠️ 不支持的通知类型: {notify_type}")

class WeiboChaohuaSignin:
    def __init__(self, cookie, account_index=1, total_accounts=1):
        self.account_index = account_index
        self.total_accounts = total_accounts
        self.account_name = f"账户{account_index}"
        self.user_info = {}

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'sec-ch-ua': '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
        })

        # 解析Cookie字符串为字典
        cookie_dict = self._parse_cookie(cookie)
        self.session.cookies.update(cookie_dict)

        # 访问微博首页获取最新的XSRF-TOKEN和uid
        self._init_session()

        # 配置 (为了对抗微博日益严格的 382001 风控，显著减慢速度)
        self.sign_interval = 8.0    # 签到基础间隔(秒)，实际还会加上随机延迟
        self.account_interval = 10  # 账户间间隔(秒)
        self.max_retries = 2        # 单个超话最大重试次数 (降低无意义重试)
        self.batch_size = 6         # 每批签到数量 (原10，现降为6)
        self.batch_pause = 60       # 批次间休息秒数 (原30，现增至60)

    def _parse_cookie(self, cookie):
        """解析Cookie字符串为字典"""
        cookies = {}
        try:
            cookie = cookie.strip().replace('\n', '').replace('\r', '')
            if isinstance(cookie, bytes):
                cookie = cookie.decode('utf-8', errors='ignore')
            for item in cookie.split(';'):
                item = item.strip()
                if '=' in item:
                    key, value = item.split('=', 1)
                    cookies[key.strip()] = value.strip()
        except Exception as e:
            self.log(f"Cookie解析失败: {str(e)}", 'ERROR')
        return cookies

    def _init_session(self):
        """访问微博首页，获取最新的XSRF-TOKEN和uid"""
        try:
            resp = self.session.get('https://weibo.com', timeout=15, allow_redirects=True)
            self.user_info['uid'] = resp.headers.get('x-log-uid', '')
            # 从Cookie jar中获取XSRF-TOKEN
            self.xsrf_token = self.session.cookies.get('XSRF-TOKEN', '')
            if self.xsrf_token:
                self.session.headers['X-XSRF-TOKEN'] = self.xsrf_token
            self.log(f"会话初始化成功 (uid: {self.user_info.get('uid', 'N/A')}, xsrf: {'有' if self.xsrf_token else '无'}, status: {resp.status_code})")
        except Exception as e:
            self.log(f"会话初始化失败: {str(e)}", 'WARNING')
            self.xsrf_token = ''
            self.user_info['uid'] = ''
    
    def get_user_info(self):
        """获取用户基本信息"""
        if self.user_info.get('name'):
            return self.user_info['name']
        sub = self.session.cookies.get('SUB', '')
        if sub:
            return f"用户{sub[:8]}..."
        return "未知用户"

    def update_user_info(self):
        """通过API获取用户昵称等信息"""
        if not self.user_info.get('uid'):
            return
        try:
            headers = {
                'Referer': f'https://weibo.com/u/{self.user_info["uid"]}',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/plain, */*',
            }
            resp = self.session.get(
                f'https://weibo.com/ajax/profile/info?uid={self.user_info["uid"]}',
                headers=headers,
                timeout=15
            ).json()
            if resp.get('ok') == 1:
                user = resp['data']['user']
                self.user_info['name'] = user.get('screen_name', '')
                self.user_info['location'] = user.get('location', '')
                self.log(f"用户信息: {self.user_info['name']}")
        except Exception as e:
            self.log(f"获取用户信息失败: {str(e)}", 'WARNING')
    
    def log(self, message, level='INFO'):
        """日志输出"""
        timestamp = time.strftime('%H:%M:%S', time.localtime())
        symbols = {
            'INFO': '[i]',
            'SUCCESS': '[+]',
            'ERROR': '[-]',
            'WARNING': '[!]'
        }

        # 多账户时显示账户信息
        account_prefix = f"[{self.account_name}] " if self.total_accounts > 1 else ""
        try:
            print(f"[{timestamp}] {symbols.get(level, '[i]')} {account_prefix}{message}")
        except UnicodeEncodeError:
            # Windows GBK fallback
            print(f"[{timestamp}] {symbols.get(level, '[i]')} {account_prefix}{message}".encode('gbk', errors='replace').decode('gbk'))
    
    def fetch_chaohua_list(self, page=1, collected=None):
        """获取超话列表"""
        if collected is None:
            collected = []

        self.log(f"正在获取第 {page} 页超话列表...")

        url = f"https://weibo.com/ajax/profile/topicContent"
        params = {
            'tabid': '231093_-_chaohua',
            'page': page
        }

        try:
            headers = {
                'Referer': f'https://weibo.com/u/page/follow/{self.user_info.get("uid", "")}/231093_-_chaohua',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/plain, */*',
            }

            response = self.session.get(url, params=params, headers=headers, timeout=15)

            if response.status_code != 200:
                self.log(f"HTTP状态码: {response.status_code}, 响应内容: {response.text[:300]}", 'WARNING')
                raise Exception(f"HTTP Error: {response.status_code}")
            
            # 检查响应内容
            if not response.text:
                raise Exception("响应内容为空")
            
            try:
                data = response.json()
            except json.JSONDecodeError as e:
                self.log(f"JSON解析失败，响应内容: {response.text[:200]}...", 'ERROR')
                raise Exception(f"JSON解析失败: {str(e)}")
            
            if data.get('ok') != 1:
                error_msg = data.get('msg', '') or '未知错误(空msg)'
                ok_code = data.get('ok', '')
                # ok=-100 表示需要登录
                if ok_code == -100 or 'login' in str(data.get('url', '')).lower():
                    raise Exception(f"Cookie已过期或无效，需要重新获取 (ok={ok_code}, 重定向到登录页)")
                self.log(f"API原始响应: {json.dumps(data, ensure_ascii=False)[:300]}", 'WARNING')
                if 'login' in error_msg.lower() or 'cookie' in error_msg.lower():
                    raise Exception(f"登录状态失效，请更新Cookie: {error_msg}")
                raise Exception(f"API返回错误: {error_msg}")
            
            api_data = data.get('data', {})
            chaohua_list = api_data.get('list', [])
            
            if not chaohua_list:
                return collected
            
            # 提取超话ID和名称
            for item in chaohua_list:
                oid = item.get('oid', '')
                if oid.startswith('1022:'):
                    chaohua_id = oid[5:]  # 去掉前缀 "1022:"
                    chaohua_name = item.get('topic_name', '')
                    if chaohua_id and chaohua_name:
                        collected.append({
                            'id': chaohua_id,
                            'name': chaohua_name
                        })
            
            # 检查是否还有下一页
            max_page = api_data.get('max_page', 1)
            if page < max_page:
                time.sleep(0.8)
                return self.fetch_chaohua_list(page + 1, collected)
            
            return collected
            
        except requests.exceptions.RequestException as e:
            self.log(f"网络请求失败: {str(e)}", 'ERROR')
            raise
        except Exception as e:
            self.log(f"获取超话列表失败: {str(e)}", 'ERROR')
            raise
    
    def sign_chaohua(self, chaohua_id, chaohua_name):
        """签到单个超话"""
        url = "https://weibo.com/p/aj/general/button"

        params = {
            'ajwvr': '6',
            'api': 'http://i.huati.weibo.com/aj/super/checkin',
            'texta': '签到',
            'textb': '已签到',
            'status': '0',
            'id': chaohua_id,
            'location': 'page_100808_super_index',
            'timezone': 'GMT 0800',
            'lang': 'zh-cn',
            'plat': 'Win32',
            'ua': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
            'screen': '2560*1440',
            '__rnd': int(time.time() * 1000)
        }

        try:
            headers = {
                'Referer': f'https://weibo.com/p/{chaohua_id}/super_index',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json, text/plain, */*',
            }

            response = self.session.get(url, params=params, headers=headers, timeout=15)
            
            if response.status_code != 200:
                return {'success': False, 'msg': f'HTTP错误: {response.status_code}'}
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                return {'success': False, 'msg': '响应格式错误'}
            
            code = str(data.get('code', ''))
            msg = data.get('msg', '未知错误')

            # 签到成功时从 data 中取详细消息
            if code == '100000' and 'data' in data:
                msg = data['data'].get('tipMessage', msg)

            # 成功的状态码: 100000(签到成功), 382004(今日已签到), 382010(其他成功状态)
            success_codes = ['100000', '382004', '382010']
            is_success = code in success_codes
            
            return {
                'success': is_success,
                'code': code,
                'msg': msg,
                'already_signed': code == '382004'
            }
            
        except requests.exceptions.RequestException as e:
            return {'success': False, 'msg': f'网络请求失败: {str(e)}'}
        except Exception as e:
            return {'success': False, 'msg': f'签到失败: {str(e)}'}
    
    def run(self):
        """单个账户执行签到"""
        self.update_user_info()
        user_info = self.get_user_info()
        self.log(f"🚀 开始执行签到任务 ({user_info})")
        
        # 检查Cookie和XSRF-TOKEN
        if not self.xsrf_token:
            self.log("⚠️ 未找到XSRF-TOKEN，可能影响签到功能", 'WARNING')
        
        try:
            # 获取超话列表
            self.log("📋 正在获取超话列表...")
            chaohua_list = self.fetch_chaohua_list()
            
            if not chaohua_list:
                self.log("未获取到超话列表，请检查Cookie是否有效", 'WARNING')
                return {
                    'success': False,
                    'total': 0,
                    'success_count': 0,
                    'already_signed_count': 0,
                    'fail_count': 0
                }
            
            self.log(f"📊 成功获取到 {len(chaohua_list)} 个超话")
            
            # 开始批量签到
            success_count = 0
            already_signed_count = 0
            fail_count = 0
            failed_list = []
            
            def sign_batch(items, round_label=""):
                """签到一批超话，返回 (成功数, 已签数, 失败数, 失败列表)"""
                s_count = 0
                a_count = 0
                f_count = 0
                f_list = []
                
                consecutive_382001_failures = 0
                
                for i, chaohua in enumerate(items, 1):
                    chaohua_id = chaohua['id']
                    chaohua_name = chaohua['name']
                    
                    self.log(f"📝 {round_label}正在签到 ({i}/{len(items)}): {chaohua_name}")
                    
                    # 带重试的签到
                    result = None
                    for attempt in range(1, self.max_retries + 1):
                        result = self.sign_chaohua(chaohua_id, chaohua_name)
                        
                        # 382001 = 频率限制，等待后重试
                        if not result['success'] and result.get('code') == '382001':
                            if attempt < self.max_retries:
                                wait = 15 * attempt + random.uniform(5, 15)
                                self.log(f"⏳ [{chaohua_name}] 被频率限制，等待 {wait:.0f}秒 后重试 ({attempt}/{self.max_retries})", 'WARNING')
                                time.sleep(wait)
                            continue
                        break  # 成功或其他错误，不重试
                    
                    if result['success']:
                        consecutive_382001_failures = 0  # 成功则重置计数器
                        if result.get('already_signed'):
                            self.log(f"⚠️  [{chaohua_name}] {result['msg']}", 'WARNING')
                            a_count += 1
                        else:
                            self.log(f"✅ [{chaohua_name}] {result['msg']}", 'SUCCESS')
                            s_count += 1
                    else:
                        self.log(f"❌ [{chaohua_name}] {result['msg']}", 'ERROR')
                        f_count += 1
                        f_list.append(chaohua)
                        
                        # 熔断机制：如果连续出现 382001，直接终止后续所有签到
                        if not result['success'] and result.get('code') == '382001':
                            consecutive_382001_failures += 1
                            if consecutive_382001_failures >= 3:
                                self.log(f"⛔ 触发熔断保护：连续 {consecutive_382001_failures} 个超话触发 382001 频率限制，账号已被微博拉黑(冷却期)，提前终止今日任务！", 'ERROR')
                                # 将剩下的也算作失败
                                remaining = len(items) - i
                                if remaining > 0:
                                    self.log(f"⏩ 跳过剩余 {remaining} 个超话的签到", 'WARNING')
                                    f_count += remaining
                                    f_list.extend(items[i:])
                                break
                        else:
                            consecutive_382001_failures = 0
                    
                    # 添加随机延迟
                    if i < len(items):
                        delay = self.sign_interval + random.uniform(1.0, 3.0)
                        time.sleep(delay)
                    
                    # 每批次签到后长休息，避免频率限制
                    if i % self.batch_size == 0 and i < len(items):
                        self.log(f"💤 已完成 {i}/{len(items)}，休息 {self.batch_pause} 秒后继续...")
                        time.sleep(self.batch_pause)
                
                return s_count, a_count, f_count, f_list
            
            # ── 第一轮签到 ──
            s, a, f, failed_list = sign_batch(chaohua_list)
            success_count += s
            already_signed_count += a
            fail_count += f
            
            # ── 补签：如果有失败的，等待后再来一轮 ──
            if failed_list:
                retry_wait = 60
                self.log(f"🔄 第一轮有 {len(failed_list)} 个失败，等待 {retry_wait} 秒后开始补签...", 'WARNING')
                time.sleep(retry_wait)
                
                s2, a2, f2, still_failed = sign_batch(failed_list, round_label="[补签] ")
                success_count += s2
                already_signed_count += a2
                # 更新失败数：减去补签成功的
                fail_count = fail_count - (s2 + a2) + f2 - len(failed_list) + len(still_failed)
                fail_count = len(still_failed)  # 最终失败数 = 补签后仍然失败的
                
                if still_failed:
                    self.log(f"⚠️ 补签后仍有 {len(still_failed)} 个失败", 'WARNING')
                else:
                    self.log(f"🎉 补签全部成功！", 'SUCCESS')
            
            # 输出统计结果
            self.log("=" * 30)
            self.log("📈 签到统计结果:")
            self.log(f"✅ 签到成功: {success_count} 个")
            self.log(f"⚠️  已签过: {already_signed_count} 个") 
            self.log(f"❌ 签到失败: {fail_count} 个")
            self.log(f"📊 总计处理: {len(chaohua_list)} 个超话")
            
            if success_count > 0 or already_signed_count > 0:
                self.log("🎉 账户签到任务完成!", 'SUCCESS')
            else:
                self.log("⚠️ 没有成功签到任何超话，请检查Cookie状态", 'WARNING')
            
            return {
                'success': True,
                'total': len(chaohua_list),
                'success_count': success_count,
                'already_signed_count': already_signed_count,
                'fail_count': fail_count
            }
            
        except Exception as e:
            self.log(f"任务执行失败: {str(e)}", 'ERROR')
            # 提供一些常见问题的解决建议
            if 'cookie' in str(e).lower() or 'login' in str(e).lower():
                self.log("💡 建议: 请重新获取微博Cookie并更新环境变量", 'INFO')
            elif 'network' in str(e).lower() or 'timeout' in str(e).lower():
                self.log("💡 建议: 检查网络连接或稍后重试", 'INFO')
            
            return {
                'success': False,
                'total': 0,
                'success_count': 0,
                'already_signed_count': 0,
                'fail_count': 0,
                'error': str(e)
            }

def parse_netscape_cookies(content):
    """解析Netscape格式的Cookie文件，返回cookie字符串"""
    cookies = []
    for line in content.split('\n'):
        line = line.strip()
        # 跳过注释和空行
        if not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) >= 7:
            # 格式: domain flag path secure expiry name value
            name, value = parts[5], parts[6]
            cookies.append(f"{name}={value}")
    if cookies:
        print(f"🍪 从Netscape格式解析到 {len(cookies)} 个Cookie字段")
    return '; '.join(cookies)


def get_cookies():
    """获取Cookie配置，支持环境变量、文件读取、Netscape格式"""

    def read_file(path):
        """读取文件，自动检测Netscape格式并转换"""
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        # 检测Netscape格式
        if content.startswith('# Netscape HTTP Cookie File'):
            cookie_str = parse_netscape_cookies(content)
            print(f"📄 从 {os.path.basename(path)} 解析Netscape格式Cookie")
            return [cookie_str]
        return None  # 返回None表示不是Netscape格式，需要进一步处理

    def split_content(content):
        """拆分cookie内容为账户列表"""
        # 检测Netscape格式
        if content.startswith('# Netscape HTTP Cookie File'):
            return [parse_netscape_cookies(content)]

        cookies = []
        if '@' in content:
            cookies = [c.strip() for c in content.split('@') if c.strip()]
        elif '----' in content:
            cookies = [c.strip() for c in content.split('----') if c.strip()]
        elif '\n' in content:
            # 按换行分割
            lines = [c.strip() for c in content.split('\n') if c.strip()]
            # 如果多行且每行都像是独立的cookie字符串（长度>50），则是多账户
            if len(lines) > 1 and all(len(l) > 50 for l in lines):
                cookies = lines
            else:
                cookies = [content.strip()]
        else:
            cookies = [content.strip()]
        return cookies

    # 优先使用多账户配置
    cookies_env = os.getenv('WEIBO_COOKIES')
    if cookies_env:
        if os.path.isfile(cookies_env):
            result = read_file(cookies_env)
            if result:
                return result
            # 如果不是Netscape格式，read_file返回None，继续用原始值
            with open(cookies_env, 'r', encoding='utf-8') as f:
                cookies_env = f.read()
        cookies = split_content(cookies_env)
        if cookies:
            print(f"🔍 检测到多账户配置，共 {len(cookies)} 个账户")
            return cookies

    # 单账户配置
    cookie_env = os.getenv('WEIBO_COOKIE')
    if cookie_env:
        if os.path.isfile(cookie_env):
            result = read_file(cookie_env)
            if result:
                return result
            with open(cookie_env, 'r', encoding='utf-8') as f:
                cookie_env = f.read().strip()
        print("🔍 检测到单账户配置")
        return [cookie_env]

    # 检查当前目录及 data/ 子目录下是否有 cookie 文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [
        os.path.join(script_dir, 'data'),  # 优先：data/ 子目录（Docker 挂载）
        script_dir,                         # 兼容：脚本同目录
    ]
    for search_dir in search_dirs:
      for cookie_file in ['weibo.com_cookies.txt', 'cookie.txt', 'cookies.txt', 'cookie', '.cookie']:
        cookie_path = os.path.join(search_dir, cookie_file)
        if os.path.isfile(cookie_path):
            result = read_file(cookie_path)
            if result:
                return result
            # 不是Netscape格式，按普通方式处理
            with open(cookie_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if content:
                cookies = split_content(content)
                if len(cookies) > 1:
                    print(f"📄 从 {cookie_file} 读取到 {len(cookies)} 个账户的Cookie")
                else:
                    print(f"📄 从 {cookie_file} 读取Cookie")
                return cookies

    return []

def main():
    """主函数"""
    print("=" * 60)
    print("🌟 微博超话批量签到脚本 v1.2")
    print("📅 支持多账户批量签到")
    print("=" * 60)
    
    # 检查是否在青龙面板环境中
    if not os.getenv('QL_DIR'):
        print("⚠️  建议在青龙面板中运行此脚本")
    
    # 获取Cookie配置
    cookies = get_cookies()
    
    if not cookies:
        print("❌ 请设置环境变量 WEIBO_COOKIE 或 WEIBO_COOKIES")
        print("💡 单账户: WEIBO_COOKIE=\"cookie内容\"")
        print("💡 多账户: WEIBO_COOKIES=\"cookie1@cookie2@cookie3\" 或换行分割")
        sys.exit(1)
    
    # 总体统计
    total_accounts = len(cookies)
    all_results = []
    
    print(f"🎯 开始执行批量签到任务，共 {total_accounts} 个账户")
    print("=" * 60)
    
    # 逐个账户执行签到
    for i, cookie in enumerate(cookies, 1):
        if not cookie or len(cookie) < 50:  # 简单验证Cookie长度
            print(f"❌ 账户{i} Cookie无效，跳过")
            continue
        
        try:
            # 创建签到实例
            signin = WeiboChaohuaSignin(cookie, i, total_accounts)
            
            # 执行签到
            result = signin.run()
            all_results.append({
                'account': i,
                'result': result
            })
            
            # 账户间延迟
            if i < total_accounts:
                print(f"⏱️  等待 {signin.account_interval} 秒后处理下一个账户...")
                time.sleep(signin.account_interval)
            
        except Exception as e:
            print(f"❌ 账户{i} 执行失败: {str(e)}")
            all_results.append({
                'account': i,
                'result': {
                    'success': False,
                    'total': 0,
                    'success_count': 0,
                    'already_signed_count': 0,
                    'fail_count': 0,
                    'error': str(e)
                }
            })
        
        print("-" * 60)
    
    # 输出总体统计
    print("🏆 全部账户签到完成！")
    print("=" * 60)
    print("📊 总体统计结果:")
    
    total_success = 0
    total_already_signed = 0
    total_fail = 0
    total_topics = 0
    success_accounts = 0
    
    for account_result in all_results:
        account = account_result['account']
        result = account_result['result']
        
        if result['success']:
            success_accounts += 1
            total_success += result['success_count']
            total_already_signed += result['already_signed_count']
            total_fail += result['fail_count']
            total_topics += result['total']
            
            print(f"✅ 账户{account}: 成功{result['success_count']} | 已签{result['already_signed_count']} | 失败{result['fail_count']} | 总计{result['total']}")
        else:
            error_msg = result.get('error', '未知错误')
            print(f"❌ 账户{account}: 执行失败 - {error_msg}")
    
    print("-" * 60)
    print(f"🎯 成功执行账户: {success_accounts}/{total_accounts}")
    print(f"✅ 总签到成功: {total_success} 个超话")
    print(f"⚠️  总已签过: {total_already_signed} 个超话")
    print(f"❌ 总签到失败: {total_fail} 个超话")
    print(f"📊 总处理超话: {total_topics} 个超话")
    
    # 执行结果判断
    if success_accounts > 0:
        print("🎉 批量签到任务执行完成！")
        if total_success > 0:
            print(f"🌟 本次新增签到 {total_success} 个超话")
    else:
        print("⚠️  所有账户均执行失败，尝试自动续期Cookie...")
        
        # 尝试自动续期
        renewed = False
        try:
            from check_cookie import do_renew
            renewed = do_renew("签到时检测到所有账户Cookie已失效")
        except Exception as e:
            print(f"⚠️ 自动续期调用失败: {e}")
        
        if renewed:
            print("✅ Cookie已自动更新，重新执行签到...")
            # 重新读取Cookie并签到
            new_cookies = get_cookies()
            if new_cookies:
                for i, cookie in enumerate(new_cookies, 1):
                    if not cookie or len(cookie) < 50:
                        continue
                    try:
                        signin = WeiboChaohuaSignin(cookie, i, len(new_cookies))
                        result = signin.run()
                        if result['success'] and result['success_count'] > 0:
                            success_accounts += 1
                            total_success += result['success_count']
                            total_already_signed += result['already_signed_count']
                            total_fail += result['fail_count']
                            total_topics += result['total']
                    except Exception as e:
                        print(f"❌ 续期后重试账户{i}失败: {e}")
        
        if success_accounts == 0:
            print("⚠️  Cookie续期失败或续期后签到仍失败")
            send_notify(
                '微博超话签到失败 - Cookie已过期',
                f'所有{total_accounts}个账户均签到失败，请手动更新Cookie文件。\n时间: {time.strftime("%Y-%m-%d %H:%M:%S")}'
            )
            sys.exit(1)

    # 发送每日签到汇总通知
    notify_msg = []
    notify_msg.append(f"⏱ 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    notify_msg.append(f"👤 账户: {success_accounts}/{total_accounts} 成功")
    notify_msg.append(f"✅ 签到成功: {total_success} 个超话")
    notify_msg.append(f"⚠ 已签过: {total_already_signed} 个超话")
    if total_fail > 0:
        notify_msg.append(f"❌ 签到失败: {total_fail} 个超话")
    send_notify('微博超话签到完成', '\n'.join(notify_msg))

    print("=" * 60)

if __name__ == "__main__":
    # 开始时间记录
    start_time = time.time()
    
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  用户中断执行")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 程序执行异常: {str(e)}")
        sys.exit(1)
    finally:
        # 结束时间统计
        end_time = time.time()
        duration = int(end_time - start_time)
        print(f"⏱️  总耗时: {duration} 秒")

