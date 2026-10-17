"""离线回归测试：所有 HTTP 和 Telegram 调用均模拟，Cookie 为虚构值。"""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from requests.adapters import BaseAdapter

import cookie_store as store
import heartbeat
import login
import check_cookie
import wb


class OfflineAdapter(BaseAdapter):
    """走真实 Requests Cookie 提取流程，只替换网络传输。"""
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        if not self.replies:
            raise AssertionError('Unexpected HTTP request')
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body, headers = reply
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.headers.update(headers)
        response._content = (json.dumps(body) if isinstance(body, (dict, list)) else body).encode()
        response._content_consumed = True
        message = Message()
        for name, value in headers.items():
            message.add_header(name, value)
        response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
        return response

    def close(self):
        pass


def offline_session(jar, replies):
    session = store.make_session(jar)
    adapter = OfflineAdapter(replies)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session, adapter


HOME = (200, '<html>Weibo</html>', {})
VALID = (200, {'ok': 1, 'data': {'list': []}}, {})
INVALID = (200, {'ok': -100}, {})


class CookieLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='weibo-tests-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.path = self.directory / 'weibo.com_cookies.txt'
        self.patches = [
            patch.object(store, 'BASE_DIR', self.directory),
            patch.object(login, 'COOKIE_FILE', str(self.path)),
            patch.object(check_cookie, 'COOKIE_FILE', str(self.path)),
            patch.dict(os.environ, {}, clear=True),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.jar = store.parse_cookie_content('SUB=old-fake-token; XSRF-TOKEN=fake-xsrf')
        self.account = store.CookieAccount(self.jar, self.path, '测试账户')
        self.account.persist(self.jar)

    def test_real_metadata_http_only_and_session_cookie_roundtrip(self):
        expiry = int(time.time()) + 123456
        content = ('# Netscape HTTP Cookie File\n'
                   f'#HttpOnly_.weibo.com\tTRUE\t/\tTRUE\t{expiry}\tSUB\tfake-token\n'
                   'weibo.com\tFALSE\t/\tFALSE\t0\tXSRF-TOKEN\tfake-xsrf\n'
                   f'.sina.com.cn\tTRUE\t/sso\tTRUE\t{expiry}\tSUB\tfake-sina\n')
        store.save_cookie_jar(store.parse_cookie_content(content), self.path)
        loaded = store.read_cookie_file(self.path)
        sub = [cookie for cookie in loaded if cookie.domain == '.weibo.com'][0]
        self.assertEqual(sub.expires, expiry)
        self.assertTrue(sub.has_nonstandard_attr('HTTPOnly'))
        xsrf = [cookie for cookie in loaded if cookie.name == 'XSRF-TOKEN'][0]
        self.assertIsNone(xsrf.expires)
        self.assertFalse(xsrf.secure)
        self.assertFalse(xsrf.domain_specified)
        self.assertEqual(len(loaded), 3)
        self.assertNotIn('fake-sina', store.cookie_header(loaded))
        self.assertEqual(store.cookie_value(loaded, 'SUB'), 'fake-token')

    def test_expired_cookie_not_sent(self):
        self.jar.set('ALF', 'expired-fake', domain='.weibo.com', expires=int(time.time()) - 60)
        self.assertNotIn('ALF=', store.cookie_header(self.jar))

    def test_plain_cookie_has_no_fabricated_expiry_or_unscoped_domain(self):
        self.assertTrue(all(cookie.expires is None and cookie.domain for cookie in self.jar))

    def test_atomic_save_failure_preserves_previous_file(self):
        original = self.path.read_bytes()
        with patch.object(store.os, 'replace', side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError):
                store.save_cookie_jar(store.parse_cookie_content('SUB=new-fake'), self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob('.cookies-*')), [])

    def test_environment_cache_is_reused_after_renewal(self):
        with patch.dict(os.environ, {'WEIBO_COOKIE': 'SUB=env-old-fake'}):
            account = store.load_accounts()[0]
            account.persist(store.parse_cookie_content('SUB=env-refreshed-fake'))
            self.assertEqual(store.cookie_value(store.load_accounts()[0].jar, 'SUB'), 'env-refreshed-fake')
        with patch.dict(os.environ, {'WEIBO_COOKIE': 'SUB=different-fake'}):
            self.assertEqual(store.cookie_value(store.load_accounts()[0].jar, 'SUB'), 'different-fake')

    def test_multiple_accounts_cache_independent(self):
        with patch.dict(os.environ, {'WEIBO_COOKIES': 'SUB=first-fake@SUB=second-fake'}):
            accounts = store.load_accounts()
            accounts[1].persist(store.parse_cookie_content('SUB=second-refreshed-fake'))
            loaded = store.load_accounts()
            self.assertEqual(store.cookie_value(loaded[0].jar, 'SUB'), 'first-fake')
            self.assertEqual(store.cookie_value(loaded[1].jar, 'SUB'), 'second-refreshed-fake')
            self.assertNotEqual(loaded[0].path, loaded[1].path)

    def test_environment_file_and_legacy_file_resolve_same_source(self):
        with patch.dict(os.environ, {'WEIBO_COOKIE': str(self.path)}):
            self.assertEqual(store.load_accounts()[0].path, self.path)
        self.assertEqual(store.load_accounts()[0].path, self.path)
        (self.directory / 'data').mkdir()
        self.assertEqual(store.default_cookie_file(), self.path)

    def test_task_lock_rejects_overlap_and_releases(self):
        with store.task_lock():
            with self.assertRaises(store.TaskBusy):
                with store.task_lock():
                    pass
        with store.task_lock():
            pass

    def test_validation_only_confirms_successful_protected_response(self):
        for response, expected in [
            (VALID, True), (INVALID, False),
            ((200, {'ok': 0, 'msg': 'invalid uid'}, {}), None),
            ((200, {'ok': 1, 'data': {}}, {}), None),
            ((200, {'ok': 0, 'url': 'https://passport.weibo.com/login'}, {}), False),
            ((302, '', {'Location': 'https://passport.weibo.com/login'}), False),
            ((503, {'ok': -100}, {}), None),
            ((429, {}, {}), None),
            ((200, '<html>unexpected page</html>', {}), None),
        ]:
            with self.subTest(response=response):
                with offline_session(self.jar, [HOME, response])[0] as session:
                    self.assertIs(store.validate_session(session)[0], expected)

    def test_homepage_failure_and_timeout_are_unknown(self):
        for response in [(503, '', {}), requests.Timeout('offline')]:
            with offline_session(self.jar, [response])[0] as session:
                self.assertIsNone(store.validate_session(session)[0])

    def test_heartbeat_persists_real_set_cookie_refresh(self):
        session, adapter = offline_session(self.jar, [
            (200, '', {'Set-Cookie': 'SUB=new-fake-token; Domain=.weibo.com; Path=/; Secure; HttpOnly; Max-Age=2592000'}), VALID,
        ])
        with patch.object(heartbeat, 'make_session', return_value=session):
            self.assertEqual(heartbeat.main(), 0)
        loaded = store.read_cookie_file(self.path)
        self.assertEqual(store.cookie_value(loaded, 'SUB'), 'new-fake-token')
        self.assertEqual(adapter.sent[1].headers['Cookie'].count('SUB='), 1)
        expiry = store.key_expiries(loaded)['SUB']
        self.assertLess(abs(expiry - time.time() - 2592000), 3)

    def test_heartbeat_preserves_old_file_on_invalid_or_unknown(self):
        original = self.path.read_bytes()
        for response in [INVALID, (503, {}, {}), (200, {'ok': 0}, {})]:
            session, _ = offline_session(self.jar, [HOME, response])
            with patch.object(heartbeat, 'make_session', return_value=session):
                self.assertEqual(heartbeat.main(), 1)
            self.assertEqual(self.path.read_bytes(), original)

    def test_heartbeat_checks_every_account(self):
        second = store.CookieAccount(self.jar, self.directory / 'second.txt', '测试账户2')
        sessions = [offline_session(self.jar, [HOME, VALID])[0] for _ in range(2)]
        with patch.object(heartbeat, 'load_accounts', return_value=[self.account, second]), \
             patch.object(heartbeat, 'make_session', side_effect=sessions):
            self.assertEqual(heartbeat.main(), 0)
        self.assertTrue(second.path.is_file())

    def test_health_check_refreshes_before_deciding_to_scan(self):
        self.jar.set('ALF', 'fake-alf', domain='.weibo.com', path='/', expires=int(time.time()) + 86400)
        self.account.persist(self.jar)
        session, _ = offline_session(self.jar, [
            (200, '', {'Set-Cookie': 'ALF=new-fake-alf; Domain=.weibo.com; Path=/; Max-Age=2592000'}), VALID,
        ])
        with patch.object(check_cookie, 'make_session', return_value=session), \
             patch.object(check_cookie, 'do_renew') as renew:
            self.assertEqual(check_cookie.main(), 0)
            renew.assert_not_called()

    def test_health_check_unknown_does_not_scan_even_with_expired_metadata(self):
        self.jar.set('ALF', 'expired-fake', domain='.weibo.com', expires=int(time.time()) - 100)
        # 写入过期元数据，模拟从浏览器导出的旧文件。
        self.path.write_text('# Netscape HTTP Cookie File\n.weibo.com\tTRUE\t/\tTRUE\t1\tALF\texpired-fake\n', encoding='utf-8')
        original = self.path.read_bytes()
        session, _ = offline_session(self.jar, [HOME, (503, {}, {})])
        with patch.object(check_cookie, 'make_session', return_value=session), \
             patch.object(check_cookie, 'do_renew') as renew:
            self.assertEqual(check_cookie.main(), 1)
            renew.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_health_check_renews_confirmed_invalid_account(self):
        session, _ = offline_session(self.jar, [HOME, INVALID])
        with patch.object(check_cookie, 'make_session', return_value=session), \
             patch.object(check_cookie, 'do_renew', return_value=True) as renew:
            self.assertEqual(check_cookie.main(), 0)
            self.assertEqual(renew.call_args.kwargs['account'].path, self.path)

    def test_health_check_renews_when_valid_but_still_near_expiry(self):
        self.jar.set('ALF', 'near-expiry-fake', domain='.weibo.com', path='/', expires=int(time.time()) + 86400)
        self.account.persist(self.jar)
        session, _ = offline_session(self.jar, [HOME, VALID])
        with patch.object(check_cookie, 'make_session', return_value=session), \
             patch.object(check_cookie, 'do_renew', return_value=True) as renew:
            self.assertEqual(check_cookie.main(), 0)
            renew.assert_called_once()

    def test_deleted_cookie_is_removed_from_persisted_jar(self):
        self.jar.set('obsolete', 'fake', domain='.weibo.com', path='/')
        session, _ = offline_session(self.jar, [
            (200, '', {'Set-Cookie': 'obsolete=; Domain=.weibo.com; Path=/; Max-Age=0'}), VALID,
        ])
        with patch.object(heartbeat, 'make_session', return_value=session):
            self.assertEqual(heartbeat.main(), 0)
        self.assertNotIn('obsolete=', store.cookie_header(store.read_cookie_file(self.path)))

    def test_renewal_updates_cache_and_notification_failure_is_nonfatal(self):
        session = store.make_session('SUB=renewed-fake')
        with patch.object(login, 'SESSION', session), \
             patch.object(login, 'TG_TOKEN', 'fake-token'), \
             patch.object(login, 'TG_CHAT_ID', 'fake-chat'), \
             patch.object(check_cookie, 'get_qr_code', return_value=('fake-qr', b'fake-image')), \
             patch.object(check_cookie, 'send_telegram_photo', return_value=True), \
             patch.object(check_cookie, 'send_telegram_message', return_value=False), \
             patch.object(check_cookie, 'poll_qr_login', return_value='fake-alt'), \
             patch.object(check_cookie, 'process_login'):
            self.assertTrue(check_cookie.do_renew('offline', trigger_sign=False, account=self.account))
        self.assertEqual(store.cookie_value(self.account.jar, 'SUB'), 'renewed-fake')

    def test_telegram_network_failure_returns_false(self):
        with patch.object(login, 'TG_TOKEN', 'fake-token'), \
             patch.object(login, 'TG_CHAT_ID', 'fake-chat'), \
             patch.object(login.requests, 'post', side_effect=requests.Timeout('offline')):
            self.assertFalse(login.send_telegram_message('offline'))

    def test_login_preserves_cross_domain_metadata_after_validation(self):
        replies = [
            (200, 'STK_1({"crossDomainUrlList":["https://weibo.com/sso?ticket=fake"]})', {}),
            (200, '', {'Set-Cookie': 'SUB=login-fake; Domain=.weibo.com; Path=/; Secure; HttpOnly; Max-Age=2592000'}),
            HOME, VALID,
        ]
        session, _ = offline_session('', replies)
        session.cookies.set('SUB', 'sina-fake', domain='.sina.com.cn', path='/sso', expires=int(time.time()) + 86400)
        with patch.object(login, 'SESSION', session):
            login.process_login('fake-alt')
            login.save_cookie_netscape()
        saved = store.read_cookie_file(self.path)
        self.assertEqual(store.cookie_value(saved, 'SUB'), 'login-fake')
        self.assertEqual(len([cookie for cookie in saved if cookie.name == 'SUB']), 2)
        self.assertLess(abs(store.key_expiries(saved)['SUB'] - time.time() - 2592000), 3)

    def test_failed_login_validation_preserves_existing_file(self):
        original = self.path.read_bytes()
        replies = [
            (200, 'STK_1({"crossDomainUrlList":["https://weibo.com/sso?ticket=fake"]})', {}),
            (200, '', {'Set-Cookie': 'SUB=login-fake; Domain=.weibo.com; Path=/'}), HOME, INVALID,
        ]
        session, _ = offline_session('', replies)
        with patch.object(login, 'SESSION', session):
            with self.assertRaisesRegex(Exception, '登录验证未通过'):
                login.process_login('fake-alt')
        self.assertEqual(self.path.read_bytes(), original)

    def test_signing_persists_refreshed_cookie_after_authenticated_request(self):
        session, _ = offline_session(self.jar, [
            (200, '', {'Set-Cookie': 'SUB=signing-fake; Domain=.weibo.com; Path=/; Max-Age=2592000'}), VALID,
        ])
        with patch.object(wb, 'make_session', return_value=session):
            result = wb.WeiboChaohuaSignin(self.account).run()
        self.assertTrue(result['success'])
        self.assertEqual(store.cookie_value(store.read_cookie_file(self.path), 'SUB'), 'signing-fake')

    def test_signing_marks_only_explicit_auth_failure(self):
        for response, expected in [(INVALID, True), ((503, '', {}), False), (requests.Timeout('offline'), False)]:
            with self.subTest(response=response):
                original = self.path.read_bytes()
                session, _ = offline_session(self.jar, [HOME, response])
                with patch.object(wb, 'make_session', return_value=session):
                    result = wb.WeiboChaohuaSignin(self.account).run()
                self.assertFalse(result['success'])
                self.assertIs(result['auth_expired'], expected)
                self.assertEqual(self.path.read_bytes(), original)

    def test_signing_network_failure_does_not_trigger_qr(self):
        fake = MagicMock()
        fake.run.return_value = {'success': False, 'error': 'timeout', 'auth_expired': False}
        with patch.object(wb, 'WeiboChaohuaSignin', return_value=fake), \
             patch.object(wb, 'send_notify'), patch.object(check_cookie, 'do_renew') as renew:
            with self.assertRaises(SystemExit):
                wb.main(accounts=[self.account])
            renew.assert_not_called()

    def test_signing_renews_only_failed_account_once(self):
        second = store.CookieAccount(self.jar, self.directory / 'second.txt', '测试账户2')
        second.persist(self.jar)
        success = {'success': True, 'total': 0, 'success_count': 0, 'already_signed_count': 0, 'fail_count': 0}
        failed = {'success': False, 'auth_expired': True}
        signers = [MagicMock(), MagicMock(), MagicMock()]
        for signer, result in zip(signers, [success, failed, success]):
            signer.run.return_value = result
        with patch.object(wb, 'WeiboChaohuaSignin', side_effect=signers), \
             patch.object(wb, 'send_notify'), patch.object(wb.time, 'sleep'), \
             patch.object(check_cookie, 'do_renew', return_value=True) as renew:
            wb.main(accounts=[self.account, second])
            renew.assert_called_once()
            self.assertIs(renew.call_args.kwargs['account'], second)

    def test_signing_after_renewal_does_not_recursively_scan(self):
        signer = MagicMock()
        signer.run.return_value = {'success': False, 'auth_expired': True}
        with patch.object(wb, 'WeiboChaohuaSignin', return_value=signer), \
             patch.object(wb, 'send_notify'), patch.object(check_cookie, 'do_renew') as renew:
            with self.assertRaises(SystemExit):
                wb.main(accounts=[self.account], allow_renew=False)
            renew.assert_not_called()


    def test_notify_failure_does_not_log_token(self):
        error = requests.ConnectionError('Max retries exceeded with url: /botSECRET-fake/sendMessage')
        with patch.dict(os.environ, {'TG_TOKEN': 'SECRET-fake', 'TG_CHAT_ID': '1'}), \
             patch.object(wb.requests, 'post', side_effect=error):
            wb.send_notify('t', 'c', 'telegram')
        self.assertNotIn('SECRET-fake', self.output.getvalue())

    def test_single_cookie_variable_is_not_split(self):
        with patch.dict(os.environ, {'WEIBO_COOKIE': 'SUB=a@b;\nSUBP=c'}):
            accounts = store.load_accounts()
        self.assertEqual(len(accounts), 1)
        self.assertEqual(store.cookie_value(accounts[0].jar, 'SUBP'), 'c')

    def test_qr_poll_survives_transient_network_error(self):
        session, _ = offline_session('', [
            requests.Timeout('offline'),
            (200, 'STK_1({"retcode":20000000,"data":{"alt":"fake-alt"}})', {}),
        ])
        with patch.object(login, 'SESSION', session), patch.object(login.time, 'sleep'):
            self.assertEqual(login.poll_qr_login('fake-qr', timeout_seconds=60), 'fake-alt')

    def _signer(self, replies):
        session, adapter = offline_session(self.jar, [(200, '', {'x-log-uid': '42'})] + replies)
        with patch.object(wb, 'make_session', return_value=session):
            signer = wb.WeiboChaohuaSignin(self.account)
        signer.update_user_info = lambda: None
        return signer, adapter

    def test_second_run_skips_topics_signed_today(self):
        topics = (200, {'ok': 1, 'data': {'max_page': 1, 'list': [
            {'oid': '1022:aaa', 'topic_name': 'A'}, {'oid': '1022:bbb', 'topic_name': 'B'}]}}, {})
        signed = (200, {'code': '100000', 'msg': 'ok'}, {})
        signer, _ = self._signer([topics, signed, signed])
        with patch.object(wb.time, 'sleep'):
            first = signer.run()
        self.assertEqual(first['success_count'], 2)
        signer, adapter = self._signer([topics])
        with patch.object(wb.time, 'sleep'):
            second = signer.run()
        self.assertEqual((second['success_count'], second['already_signed_count']), (0, 2))
        self.assertEqual(len(adapter.sent), 2)  # 只有首页和超话列表，没有签到请求

    def test_circuit_breaker_skips_retry_round(self):
        topics = (200, {'ok': 1, 'data': {'max_page': 1, 'list': [
            {'oid': f'1022:t{i}', 'topic_name': f'T{i}'} for i in range(5)]}}, {})
        limited = (200, {'code': '382001', 'msg': 'limited'}, {})
        signer, adapter = self._signer([topics] + [limited] * 6)
        with patch.object(wb.time, 'sleep'):
            result = signer.run()
        self.assertEqual(result['fail_count'], 5)
        self.assertEqual(len(adapter.sent), 2 + 6)  # 3 个超话各重试 2 次后熔断，不再补签


if __name__ == '__main__':
    unittest.main()
