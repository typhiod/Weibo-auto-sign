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
from cookie_store import (
    AuthenticationError, CookieAccount, TaskBusy, cookie_header, cookie_value, data_dir,
    load_accounts, make_session, parse_cookie_content, response_requires_login, task_lock,
)

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
                # 异常文本含请求 URL，会带出 SendKey，只打印类型
                print(f"⚠️ 通知发送异常 ({type(e).__name__})")

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
                print(f"⚠️ 邮件发送异常 ({type(e).__name__})")

    elif notify_type == 'telegram':
        tg_token = os.getenv('TG_TOKEN', '')
        tg_chat_id = os.getenv('TG_CHAT_ID', '')
        if tg_token and tg_chat_id:
            try:
                # 纯文本发送，避免内容中的 _ * 等字符导致 Markdown 解析失败
                text = f"{title}\n\n{content}"
                resp = requests.post(
                    f'https://api.telegram.org/bot{tg_token}/sendMessage',
                    json={'chat_id': tg_chat_id, 'text': text},
                    timeout=15
                )
                result = resp.json()
                if result.get('ok'):
                    print(f"📧 通知已发送 (Telegram)")
                else:
                    print(f"⚠️ Telegram通知失败: {result.get('description', '')}")
            except Exception as e:
                # 异常文本含请求 URL，会带出 Bot Token，只打印类型
                print(f"⚠️ Telegram通知异常 ({type(e).__name__})")

    elif notify_type:
        print(f"⚠️ 不支持的通知类型: {notify_type}")

class WeiboChaohuaSignin:
    def __init__(self, cookie, account_index=1, total_accounts=1):
        self.account_index = account_index
        self.total_accounts = total_accounts
        self.account_name = f"账户{account_index}"
        self.user_info = {}
        self.cookie_account = cookie if isinstance(cookie, CookieAccount) else None
        self.authenticated = False
        self.auth_expired = False

        self.session = make_session(cookie.jar if self.cookie_account else cookie)
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

        # 访问微博首页获取最新的XSRF-TOKEN和uid
        self._init_session()

        # 配置 (为了对抗微博日益严格的 382001 风控，显著减慢速度)
        self.sign_interval = 8.0    # 签到基础间隔(秒)，实际还会加上随机延迟
        self.account_interval = 10  # 账户间间隔(秒)
        self.max_retries = 2        # 单个超话最大重试次数 (降低无意义重试)
        self.batch_size = 6         # 每批签到数量 (原10，现降为6)
        self.batch_pause = 60       # 批次间休息秒数 (原30，现增至60)

    def _init_session(self):
        """访问微博首页，获取最新的XSRF-TOKEN和uid"""
        try:
            resp = self.session.get('https://weibo.com', timeout=15, allow_redirects=True)
            if response_requires_login(resp):
                self.auth_expired = True
                raise AuthenticationError('首页明确要求重新登录')
            resp.raise_for_status()
            self.user_info['uid'] = resp.headers.get('x-log-uid', '')
            # 从Cookie jar中获取XSRF-TOKEN
            self.xsrf_token = cookie_value(self.session.cookies, 'XSRF-TOKEN')
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
        return f'账户{self.account_index}'

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
            if response_requires_login(response):
                raise AuthenticationError('超话列表接口要求重新登录')

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
            
            if response_requires_login(response, data):
                raise AuthenticationError('超话列表接口明确返回登录失效')
            if data.get('ok') != 1:
                error_msg = data.get('msg', '') or '未知错误(空msg)'
                self.log(f"API原始响应: {json.dumps(data, ensure_ascii=False)[:300]}", 'WARNING')
                raise Exception(f"API返回错误: {error_msg}")
            
            api_data = data.get('data', {})
            if not isinstance(api_data, dict) or not isinstance(api_data.get('list'), list):
                raise ValueError('超话列表响应结构异常')
            self.authenticated = True
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
            if response_requires_login(response):
                raise AuthenticationError('签到接口要求重新登录')
            
            if response.status_code != 200:
                return {'success': False, 'msg': f'HTTP错误: {response.status_code}'}
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                return {'success': False, 'msg': '响应格式错误'}
            
            code = str(data.get('code', ''))
            if response_requires_login(response, data):
                raise AuthenticationError('签到接口明确返回登录失效')
            msg = data.get('msg', '未知错误')

            # 签到成功时从 data 中取详细消息
            if code == '100000' and isinstance(data.get('data'), dict):
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
            
        except AuthenticationError:
            raise
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
            if self.auth_expired:
                raise AuthenticationError('首页明确要求重新登录')
            # 获取超话列表
            self.log("📋 正在获取超话列表...")
            chaohua_list = self.fetch_chaohua_list()
            
            if not chaohua_list:
                self.log("没有关注的超话，无需签到", 'INFO')
                return {
                    'success': True,
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
            circuit_broken = False

            # 当天前一轮已签成功的超话不再请求，避免第二段签到把请求量翻倍
            state_key = self.user_info.get('uid') or (str(self.cookie_account.path) if self.cookie_account else '')
            signed_today = load_signed_today(state_key) if state_key else set()
            pending_list = [item for item in chaohua_list if item['id'] not in signed_today]
            skipped = len(chaohua_list) - len(pending_list)
            if skipped:
                already_signed_count += skipped
                self.log(f"⏭️ 今日已签 {skipped} 个超话，本轮跳过，剩余 {len(pending_list)} 个")

            def sign_batch(items, round_label=""):
                """签到一批超话，返回 (成功数, 已签数, 失败数, 失败列表)"""
                nonlocal circuit_broken
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
                        if state_key:
                            try:
                                mark_signed_today(state_key, chaohua_id)
                            except (OSError, ValueError) as error:
                                self.log(f"签到记录保存失败 ({type(error).__name__})", 'WARNING')
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
                                circuit_broken = True
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
            s, a, f, failed_list = sign_batch(pending_list)
            success_count += s
            already_signed_count += a
            fail_count += f

            # ── 补签：如果有失败的，等待后再来一轮；已熔断则不再请求 ──
            if failed_list and circuit_broken:
                self.log(f"⛔ 已触发熔断，跳过补签，{len(failed_list)} 个超话留给下一段签到", 'WARNING')
            elif failed_list:
                retry_wait = 60
                self.log(f"🔄 第一轮有 {len(failed_list)} 个失败，等待 {retry_wait} 秒后开始补签...", 'WARNING')
                time.sleep(retry_wait)
                
                s2, a2, f2, still_failed = sign_batch(failed_list, round_label="[补签] ")
                success_count += s2
                already_signed_count += a2
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
            self.auth_expired = isinstance(e, AuthenticationError)
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
                'error': str(e),
                'auth_expired': self.auth_expired,
            }
        finally:
            # 整个签到会话结束后保存刷新；认证失效或未确认时保留旧文件。
            if self.cookie_account and self.authenticated and not self.auth_expired:
                try:
                    self.cookie_account.persist(self.session.cookies)
                    self.log('已保存服务器更新的 Cookie')
                except (OSError, ValueError) as error:
                    self.log(f'Cookie 保存失败 ({type(error).__name__})', 'WARNING')
            self.session.close()

def _signed_state_path():
    return data_dir() / '.signed_today.json'


def _read_signed_state():
    try:
        state = json.loads(_signed_state_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    # 跨天自动作废，按容器本地日期（默认北京时间）计算
    if not isinstance(state, dict) or state.get('date') != time.strftime('%Y-%m-%d'):
        return {}
    return state


def load_signed_today(key):
    """返回该账户今天已签成功（含已签过）的超话 ID 集合。"""
    accounts = _read_signed_state().get('accounts', {})
    return set(accounts.get(key, [])) if isinstance(accounts, dict) else set()


def mark_signed_today(key, chaohua_id):
    state = _read_signed_state()
    accounts = state.get('accounts') if isinstance(state.get('accounts'), dict) else {}
    ids = set(accounts.get(key, []))
    ids.add(chaohua_id)
    accounts[key] = sorted(ids)
    path = _signed_state_path()
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps({'date': time.strftime('%Y-%m-%d'), 'accounts': accounts}),
                         encoding='utf-8')
    os.replace(temporary, path)


def parse_netscape_cookies(content):
    """兼容旧调用；正常任务使用完整 CookieJar，保留真实属性。"""
    return cookie_header(parse_cookie_content(content))


def get_cookies():
    """旧接口兼容，支持从持久缓存读取更新后的环境变量账户。"""
    return [cookie_header(account.jar) for account in load_accounts()]


def main(accounts=None, allow_renew=True):
    print("=" * 60)
    print("🌟 微博超话批量签到脚本 v1.2")
    accounts = load_accounts() if accounts is None else accounts
    if not accounts:
        print('❌ 未找到 Cookie，请配置 Cookie 文件或运行 login.py')
        sys.exit(1)

    all_results = []
    print(f'🎯 开始签到，共 {len(accounts)} 个账户')
    for index, account in enumerate(accounts, 1):
        try:
            signin = WeiboChaohuaSignin(account, index, len(accounts))
            result = signin.run()
            # 登录失效和其他执行错误分别处理，按账户续期，最多重试一次。
            if result.get('auth_expired') and allow_renew:
                from check_cookie import do_renew
                if do_renew(f'{account.source}: 服务端明确要求重新登录',
                            trigger_sign=False, account=account):
                    signin = WeiboChaohuaSignin(account.reload(), index, len(accounts))
                    result = signin.run()
            all_results.append(result)
            if index < len(accounts):
                time.sleep(signin.account_interval)
        except Exception as error:
            print(f'❌ 账户{index}执行异常 ({type(error).__name__})')
            all_results.append({'success': False, 'total': 0, 'success_count': 0,
                                'already_signed_count': 0, 'fail_count': 0,
                                'error': type(error).__name__})

    success_accounts = sum(bool(result['success']) for result in all_results)
    total_success = sum(result.get('success_count', 0) for result in all_results)
    total_already_signed = sum(result.get('already_signed_count', 0) for result in all_results)
    total_fail = sum(result.get('fail_count', 0) for result in all_results)
    total_topics = sum(result.get('total', 0) for result in all_results)
    print(f'🎯 成功执行账户: {success_accounts}/{len(accounts)}')
    print(f'✅ 签到成功: {total_success} | 已签: {total_already_signed} | 失败: {total_fail} | 总计: {total_topics}')
    if not success_accounts:
        send_notify('微博超话签到执行失败',
                    '所有账户执行失败，请查看签到日志。网络超时、限流和接口错误不会自动触发扫码。')
        sys.exit(1)
    notify_msg = (
        f'⏱ 时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n'
        f'👤 账户: {success_accounts}/{len(accounts)} 成功\n'
        f'✅ 签到成功: {total_success} 个超话\n'
        f'⚠ 已签过: {total_already_signed} 个超话\n'
        f'❌ 签到失败: {total_fail} 个超话'
    )
    send_notify('微博超话签到完成', notify_msg)


if __name__ == "__main__":
    # 开始时间记录
    start_time = time.time()
    
    try:
        with task_lock():
            main()
    except TaskBusy as error:
        print(f"[*] {error}")
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
