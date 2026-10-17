#!/usr/bin/env python3
"""定期验证登录状态，并保存服务器实际更新的 Cookie。"""
from cookie_store import TaskBusy, key_expiries, load_accounts, make_session, task_lock, validate_session


def main():
    accounts = load_accounts()
    if not accounts:
        print('[!] 未找到 Cookie，保活未执行')
        return 1

    failures = 0
    for account in accounts:
        try:
            before = key_expiries(account.jar)
            with make_session(account.jar) as session:
                valid, reason = validate_session(session)
                if valid is True:
                    account.persist(session.cookies)
                    extended = [name for name, expiry in key_expiries(session.cookies).items()
                                if expiry > before.get(name, expiry)]
                    detail = f'；过期时间延长: {", ".join(extended)}' if extended else '；未观察到关键凭证过期时间延长'
                    print(f'[+] [{account.source}] 登录有效，Cookie 已保存{detail}')
                elif valid is False:
                    failures += 1
                    print(f'[!] [{account.source}] Cookie 已失效: {reason}；请运行 check_cookie.py 扫码续期')
                else:
                    failures += 1
                    print(f'[!] [{account.source}] 保活结果无法确认: {reason}；保留原 Cookie')
        except (OSError, ValueError) as error:
            failures += 1
            print(f'[!] [{account.source}] Cookie 保存失败 ({type(error).__name__})')
    return 1 if failures else 0


if __name__ == '__main__':
    try:
        with task_lock():
            raise SystemExit(main())
    except TaskBusy as error:
        print(f'[*] {error}')
