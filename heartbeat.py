#!/usr/bin/env python3
"""Cookie保活脚本 - 定期访问微博首页保持会话"""
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wb import get_cookies

cookies = get_cookies()
if not cookies:
    print("未找到Cookie，跳过保活")
    sys.exit(0)

s = requests.Session()
for item in cookies[0].split('; '):
    if '=' in item:
        k, v = item.split('=', 1)
        s.cookies.set(k, v)

try:
    resp = s.get('https://weibo.com', timeout=15)
    print(f"保活成功: HTTP {resp.status_code}")
except Exception as e:
    print(f"保活失败: {e}")
