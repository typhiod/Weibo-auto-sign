"""Cookie 来源、持久化和登录检测；不记录 Cookie 值。"""
import copy
import hashlib
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE_DIR = Path(__file__).resolve().parent
COOKIE_NAMES = ('weibo.com_cookies.txt', 'cookie.txt', 'cookies.txt', 'cookie', '.cookie')
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')


class AuthenticationError(Exception):
    """仅用于服务端明确要求重新登录的情况。"""


class TaskBusy(Exception):
    pass


def data_dir():
    directory = BASE_DIR / 'data'
    return directory if directory.is_dir() else BASE_DIR


def default_cookie_file():
    for directory in (BASE_DIR / 'data', BASE_DIR):
        for name in COOKIE_NAMES:
            path = directory / name
            if path.is_file():
                return path
    return data_dir() / COOKIE_NAMES[0]


def parse_cookie_content(content):
    """兼容 Cookie 请求头和 Netscape 文件，包括 #HttpOnly_ 和 expiry=0。"""
    content = content.lstrip('\ufeff').strip()
    jar = requests.cookies.RequestsCookieJar()
    netscape = content.startswith('# Netscape') or any(
        len(line.split('\t')) == 7 for line in content.splitlines()
    )
    if not netscape:
        for item in content.replace('\n', '').replace('\r', '').split(';'):
            if '=' not in item:
                continue
            name, value = item.strip().split('=', 1)
            if name:
                # 请求头不含域名信息；避免创建会发送到任意域名的 Cookie。
                domain = 'weibo.com' if name in {'XSRF-TOKEN', 'WBPSESS'} else '.weibo.com'
                jar.set(name, value, domain=domain, path='/', secure=True, rest={})
        return jar

    for line_number, line in enumerate(content.splitlines(), 1):
        http_only = line.startswith('#HttpOnly_')
        if http_only:
            line = line[len('#HttpOnly_'):]
        if not line.strip() or line.startswith('#'):
            continue
        parts = line.rstrip('\r').split('\t')
        if len(parts) != 7:
            raise ValueError(f'Cookie 文件第 {line_number} 行不是有效的 Netscape 格式')
        domain, include_subdomains, path, secure, expiry, name, value = parts
        if not domain or not name:
            raise ValueError(f'Cookie 文件第 {line_number} 行缺少域名或名称')
        try:
            expires = int(expiry) if expiry and expiry != '0' else None
        except ValueError:
            raise ValueError(f'Cookie 文件第 {line_number} 行过期时间无效') from None
        domain = domain.lstrip('.')
        if include_subdomains.upper() == 'TRUE':
            domain = '.' + domain
        jar.set_cookie(Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain, domain_specified=include_subdomains.upper() == 'TRUE',
            domain_initial_dot=domain.startswith('.'), path=path or '/',
            path_specified=True, secure=secure.upper() == 'TRUE', expires=expires,
            discard=expires is None, comment=None, comment_url=None,
            rest={'HTTPOnly': ''} if http_only else {}, rfc2109=False,
        ))
    return jar


def read_cookie_file(path):
    return parse_cookie_content(Path(path).read_text(encoding='utf-8-sig'))


def cookie_header(jar):
    """仅导出发往微博首页、未过期的 Cookie，用于旧接口兼容。"""
    with requests.Session() as session:
        session.cookies.update(jar)
        request = session.prepare_request(requests.Request('GET', 'https://weibo.com/'))
        return request.headers.get('Cookie', '')


def cookie_value(jar, name):
    # 同名 Cookie 可以属于不同域名，不能直接 jar.get(name)。
    for item in cookie_header(jar).split(';'):
        key, separator, value = item.strip().partition('=')
        if separator and key == name:
            return value
    return ''


def save_cookie_jar(jar, path):
    """原子保存真实属性；保留会话 Cookie，不人为延长过期时间。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.cookies-', dir=path.parent)
    os.close(fd)
    try:
        storage = MozillaCookieJar(temporary)
        for cookie in jar:
            storage.set_cookie(copy.copy(cookie))
        storage.save(ignore_discard=True, ignore_expires=False)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class CookieAccount:
    jar: requests.cookies.RequestsCookieJar
    path: Path
    source: str

    def persist(self, jar):
        save_cookie_jar(jar, self.path)
        self.jar = copy.deepcopy(jar)

    def reload(self):
        self.jar = read_cookie_file(self.path)
        return self


def split_accounts(content):
    if content.lstrip('\ufeff').startswith('# Netscape') or '\t' in content:
        return [content]
    for separator in ('----', '@', '\n'):
        if separator in content:
            return [part.strip() for part in content.split(separator) if part.strip()]
    return [content.strip()] if content.strip() else []


def load_accounts():
    """环境变量优先；直接配置的 Cookie 使用按原始配置隔离的持久缓存。"""
    source = None
    source_path = None
    content = ''
    for variable in ('WEIBO_COOKIES', 'WEIBO_COOKIE'):
        configured = os.getenv(variable, '').strip()
        if not configured:
            continue
        source = variable
        try:
            is_file = Path(configured).is_file()
        except OSError:
            is_file = False
        if is_file:
            source_path = Path(configured).resolve()
            content = source_path.read_text(encoding='utf-8-sig')
        else:
            content = configured
        break
    if source is None:
        source_path = default_cookie_file()
        if not source_path.is_file():
            return []
        content = source_path.read_text(encoding='utf-8-sig')
        source = source_path.name

    if source == 'WEIBO_COOKIE':
        # 单账户变量不拆分，复制来的 Cookie 带换行或 @ 时不会被误判成多个账户
        parts = [content.strip()] if content.strip() else []
    else:
        parts = split_accounts(content)
    accounts = []
    for index, part in enumerate(parts, 1):
        if source_path is not None and len(parts) == 1:
            path = source_path
            jar = parse_cookie_content(part)
        else:
            # 环境变量不随扫码改变；缓存绑定原配置，改变配置会自然换用新缓存。
            digest = hashlib.sha256(part.encode('utf-8')).hexdigest()
            path = data_dir() / '.cookie_cache' / f'{digest}.txt'
            jar = read_cookie_file(path) if path.is_file() else parse_cookie_content(part)
        accounts.append(CookieAccount(jar, path, f'{source} / 账户{index}'))
    return accounts


def make_session(cookies):
    session = requests.Session()
    session.headers.update({
        'User-Agent': USER_AGENT, 'Referer': 'https://weibo.com/',
        'Accept': 'application/json, text/plain, */*',
    })
    session.cookies.update(parse_cookie_content(cookies) if isinstance(cookies, str) else cookies)
    return session


def is_login_url(url):
    parsed = urlparse(str(url))
    return (parsed.hostname in {'passport.weibo.com', 'login.sina.com.cn'}
            or parsed.path.rstrip('/').lower() in {'/login', '/signin'})


def response_requires_login(response, payload=None):
    """网络/限流错误不能被当成登录失效；只识别明确的登录响应。"""
    if response.status_code >= 500 or response.status_code in {403, 429}:
        return False
    if response.status_code == 401 or is_login_url(response.url):
        return True
    if 300 <= response.status_code < 400 and is_login_url(response.headers.get('Location', '')):
        return True
    if isinstance(payload, dict):
        return str(payload.get('ok')) == '-100' or is_login_url(payload.get('url', ''))
    return False


def validate_session(session):
    """先访问首页接收刷新，再验证已有的、需要登录的超话列表接口。"""
    try:
        homepage = session.get('https://weibo.com/', timeout=15)
        if response_requires_login(homepage):
            return False, '首页要求重新登录'
        if homepage.status_code != 200:
            return None, f'首页 HTTP {homepage.status_code}'
        response = session.get(
            'https://weibo.com/ajax/profile/topicContent',
            params={'tabid': '231093_-_chaohua', 'page': 1},
            headers={'X-Requested-With': 'XMLHttpRequest'},
            timeout=15, allow_redirects=False,
        )
        if response_requires_login(response):
            return False, '验证接口要求重新登录'
        if response.status_code != 200:
            return None, f'验证接口 HTTP {response.status_code}'
        payload = response.json()
        if response_requires_login(response, payload):
            return False, '验证接口明确返回登录失效'
        if (isinstance(payload, dict) and payload.get('ok') == 1
                and isinstance(payload.get('data'), dict)
                and isinstance(payload['data'].get('list'), list)):
            return True, '登录验证通过'
        return None, '验证接口返回未知状态'
    except requests.exceptions.RequestException as error:
        return None, f'验证请求失败 ({type(error).__name__})'
    except (ValueError, TypeError):
        return None, '验证接口返回非预期的 JSON'


def key_expiries(jar):
    expiries = {}
    for cookie in jar:
        if (cookie.domain.lstrip('.') == 'weibo.com' and cookie.path == '/'
                and cookie.name in {'ALF', 'SUB', 'SUBP'} and cookie.expires):
            expiries[cookie.name] = min(expiries.get(cookie.name, cookie.expires), cookie.expires)
    return expiries


@contextmanager
def task_lock():
    """串行化 cron/手动任务，防止旧会话覆盖刚扫码获取的 Cookie。"""
    path = data_dir() / '.cookie_tasks.lock'
    handle = open(path, 'a+b')
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise TaskBusy('已有签到、保活或扫码任务运行，本次跳过') from None
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TaskBusy('已有签到、保活或扫码任务运行，本次跳过') from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
