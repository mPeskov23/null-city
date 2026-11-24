import concurrent.futures
import email
import email.policy
import http.client
import json
import os
import re
import socketserver
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import app, mailer

REAL_SEND=mailer.send
PASSWORD='Test-only-passphrase!2026'

class TwoFactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();root=Path(cls.tmp.name);app.DB=root/'db.sqlite3';app.init_db()
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);cls.port=cls.server.server_address[1];app.ORIGIN=f'https://localhost:{cls.port}'
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem');cls.server.socket=context.wrap_socket(cls.server.socket,server_side=True)
        cls.context=ssl.create_default_context(cafile=str(root/'cert.pem'))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.counter=0
    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    def setUp(self):
        with app.RATE_LOCK: app.RATES.clear()
        self.outbox=[];self.probe_errors=[];self.fail_mail=False
        patcher=mock.patch.object(app.mailer,'send',self.fake_send);patcher.start();self.addCleanup(patcher.stop)
    def fake_send(self,to,code,lang='en',purpose='login',minutes=10):
        # Proves no SQLite write transaction is held while "SMTP" I/O is in progress.
        try:
            probe=sqlite3.connect(app.DB,timeout=0.5);probe.execute('UPDATE users SET seen=seen');probe.commit();probe.close()
        except sqlite3.OperationalError as e: self.probe_errors.append(str(e))
        if self.fail_mail: raise mailer.MailError('down')
        self.outbox.append({'to':to,'code':code,'lang':lang,'purpose':purpose})
    def tearDown(self): self.assertEqual(self.probe_errors,[],'database stayed locked during mail delivery')
    @classmethod
    def request(cls,method,path,data=None,cookie=None):
        c=http.client.HTTPSConnection('localhost',cls.port,context=cls.context,timeout=20)
        headers={'Origin':app.ORIGIN,'X-NullCity':'1','Content-Type':'application/json'}
        if cookie: headers['Cookie']=cookie
        c.request(method,'/api/'+path,json.dumps(data) if data is not None else None,headers);r=c.getresponse();body=json.loads(r.read());token=r.getheader('Set-Cookie');status=r.status;c.close()
        return status,body,token.split(';')[0] if token else None
    def register(self):
        type(self).counter+=1;email=f'twofa{self.counter}@example.test'
        status,profile,cookie=self.request('POST','register',{'email':email,'name':f'Two Factor {self.counter}','password':PASSWORD});self.assertEqual(status,200)
        return {'email':email,'id':profile['id'],'cookie':cookie}
    def relax(self,uid):
        with app.db() as c: c.execute('UPDATE twofa_log SET at=at-120 WHERE user_id=?',(uid,))
    def enable(self,user):
        status,setup,_=self.request('POST','2fa/setup',{'password':PASSWORD},user['cookie']);self.assertEqual(status,200,setup)
        status,done,_=self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':self.outbox[-1]['code']},user['cookie']);self.assertEqual(status,200,done)
        user['recovery']=done['recoveryCodes'];self.relax(user['id']);return done
    def login(self,user,**extra): return self.request('POST','login',{'email':user['email'],'password':PASSWORD,**extra})
    def challenge(self,user):
        status,body,cookie=self.login(user);self.assertEqual(status,200);self.assertTrue(body['twofa']);self.assertIsNone(cookie);return body
    def rows(self,sql,*args):
        with app.db() as c: return [tuple(r) for r in c.execute(sql,args)]

    def notices(self,fail=False):
        """Captures GDPR mails; the probe proves the operation was committed and the DB is unlocked."""
        sent=[];done=threading.Event()
        def notify(to,kind,lang='en'):
            with app.db() as c: exists=c.execute('SELECT count(*) FROM users WHERE email=?',(to,)).fetchone()[0]
            sent.append((to,kind,lang,exists));done.set()
            if fail: raise mailer.MailError('down')
        for patch in [mock.patch.object(app.mailer,'notify',notify),mock.patch.dict('os.environ',{'SMTP_HOST':'x'})]: patch.start();self.addCleanup(patch.stop)
        return sent,done
    def test_gdpr_operations_are_confirmed_by_mail(self):
        sent,done=self.notices();u=self.register()
        self.assertEqual(self.request('GET','export?lang=es',cookie=u['cookie'])[0],200);self.assertTrue(done.wait(5));done.clear()
        self.assertNotEqual(self.request('DELETE','me',{'password':'Wrong-password-here!2026'},u['cookie'])[0],200)
        self.assertEqual(self.request('DELETE','me',{'password':PASSWORD,'lang':'ru'},u['cookie'])[0],200);self.assertTrue(done.wait(5))
        self.assertEqual(sent,[(u['email'],'export','es',1),(u['email'],'erasure','ru',0)])
    def test_gdpr_mail_failure_or_anonymous_request(self):
        sent,done=self.notices(fail=True);u=self.register()
        self.assertEqual(self.request('GET','export')[0],401);self.assertEqual(sent,[])
        self.assertEqual(self.request('GET','export?lang=xx',cookie=u['cookie'])[0],200);self.assertTrue(done.wait(5));self.assertEqual(sent[0][2],'en')
        self.assertEqual(self.request('DELETE','me',{'password':PASSWORD},u['cookie'])[0],200)
    def test_gdpr_export_is_rate_limited(self):
        u=self.register()
        self.assertEqual([self.request('GET','export',cookie=u['cookie'])[0] for _ in range(11)],[200]*10+[429])

    def test_enable_then_login_requires_code_and_replay_fails(self):
        u=self.register();self.assertFalse(self.request('GET','me',cookie=u['cookie'])[1]['twofa'])
        other=self.request('POST','login',{'email':u['email'],'password':PASSWORD})[2]
        self.assertEqual(self.request('POST','2fa/setup',{'password':'Wrong-password-here!2026'},u['cookie'])[0],403)
        self.assertEqual(self.outbox,[])
        status,setup,_=self.request('POST','2fa/setup',{'password':PASSWORD,'lang':'es'},u['cookie'])
        self.assertEqual((status,setup['expiresIn']),(200,600));self.assertRegex(setup['emailHint'],r'^t\*\*\*@example\.test$')
        self.assertEqual(self.outbox[-1]['lang'],'es');self.assertEqual(self.outbox[-1]['purpose'],'enable')
        self.assertEqual(self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':'000000' if self.outbox[-1]['code']!='000000' else '000001'},u['cookie'])[0],403)
        status,done,_=self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':self.outbox[-1]['code']},u['cookie'])
        self.assertEqual(status,200);self.assertEqual(len(done['recoveryCodes']),8);self.assertEqual(len(set(done['recoveryCodes'])),8)
        self.assertTrue(self.request('GET','me',cookie=u['cookie'])[1]['twofa'])
        self.assertEqual(self.request('GET','me',cookie=other)[0],401,'other sessions are revoked')
        self.assertEqual(self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':'123456'},u['cookie'])[0],409)
        self.relax(u['id']);body=self.challenge(u)
        self.assertEqual(set(body),{'twofa','challenge','expiresIn','emailHint'});self.assertEqual(body['expiresIn'],600)
        self.assertEqual(self.outbox[-1]['purpose'],'login');code=self.outbox[-1]['code']
        status,profile,cookie=self.request('POST','login/2fa',{'challenge':body['challenge'],'code':code})
        self.assertEqual(status,200);self.assertTrue(profile['twofa']);self.assertEqual(profile['email'],u['email']);self.assertTrue(cookie.startswith('__Host-nullcity='))
        self.assertEqual(self.request('GET','me',cookie=cookie)[0],200)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':code})[0],410,'replay')

    def test_login_without_twofa_is_unchanged(self):
        u=self.register();status,body,cookie=self.login(u)
        self.assertEqual(status,200);self.assertTrue(cookie);self.assertNotIn('challenge',body);self.assertFalse(body['twofa']);self.assertEqual(self.outbox,[])
        self.assertEqual(self.request('POST','login/2fa',{'challenge':'x','code':'123456'})[0],410)

    def test_secrets_are_stored_hashed_only(self):
        u=self.register();done=self.enable(u);body=self.challenge(u);code=self.outbox[-1]['code']
        stored=json.dumps(self.rows('SELECT * FROM twofa_challenges')+self.rows('SELECT * FROM twofa_recovery'))
        for secret in [body['challenge'],code,*done['recoveryCodes'],*[c.replace('-','') for c in done['recoveryCodes']]]: self.assertNotIn(secret,stored)
        self.assertEqual(oct(os.stat(app.DB.parent/'twofa.key').st_mode&0o777),'0o600')

    def test_wrong_code_attempts_cap_is_persisted_and_kills_challenge(self):
        u=self.register();self.enable(u);body=self.challenge(u);good=self.outbox[-1]['code'];bad='000000' if good!='000000' else '000001'
        for _ in range(5): self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':bad})[0],403)
        self.assertEqual(self.rows('SELECT attempts FROM twofa_challenges WHERE user_id=? ORDER BY id DESC LIMIT 1',u['id']),[(5,)])
        status,_,cookie=self.request('POST','login/2fa',{'challenge':body['challenge'],'code':good})
        self.assertEqual(status,410);self.assertIsNone(cookie)

    def test_account_wide_attempt_limit_spans_challenges(self):
        u=self.register();self.enable(u)
        for _ in range(2):
            body=self.challenge(u);good=self.outbox[-1]['code'];bad='000000' if good!='000000' else '000001'
            self.relax(u['id'])
            for _ in range(5): self.request('POST','login/2fa',{'challenge':body['challenge'],'code':bad})
        body=self.challenge(u);status,_,cookie=self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})
        self.assertEqual(status,429);self.assertIsNone(cookie)
        self.assertEqual(self.rows('SELECT used FROM twofa_challenges WHERE token=?',app.mac('tok',body['challenge'])),[(0,)])

    def test_expired_code_is_rejected(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        with app.db() as c: c.execute('UPDATE twofa_challenges SET expires=? WHERE user_id=?',(app.now()-1,u['id']))
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})[0],410)

    def test_challenge_bound_to_user_and_purpose(self):
        a,b=self.register(),self.register();self.enable(a);self.enable(b)
        status,setup,_=self.request('POST','2fa/setup',{'password':PASSWORD},self.register()['cookie']);code=self.outbox[-1]['code']
        self.assertEqual(self.request('POST','login/2fa',{'challenge':setup['challenge'],'code':code})[0],410,'enable challenge cannot log in')
        login=self.challenge(a);login_code=self.outbox[-1]['code']
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':login['challenge'],'code':login_code},a['cookie'])[0],410,'login challenge cannot disable')
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':login['challenge'],'code':login_code},b['cookie'])[0],410,'other user')
        self.assertEqual(self.rows('SELECT attempts FROM twofa_challenges WHERE token=?',app.mac('tok',login['challenge'])),[(0,)])
        self.assertEqual(self.request('POST','login/2fa',{'challenge':login['challenge'],'code':login_code})[0],200)

    def test_concurrent_verification_yields_one_session(self):
        u=self.register();self.enable(u);body=self.challenge(u);code=self.outbox[-1]['code']
        with app.db() as c: before=c.execute('SELECT count(*) FROM sessions WHERE user_id=?',(u['id'],)).fetchone()[0]
        with concurrent.futures.ThreadPoolExecutor(6) as pool: results=list(pool.map(lambda _:self.request('POST','login/2fa',{'challenge':body['challenge'],'code':code}),range(6)))
        self.assertEqual(sorted(r[0] for r in results),[200]+[410]*5)
        with app.db() as c: self.assertEqual(c.execute('SELECT count(*) FROM sessions WHERE user_id=?',(u['id'],)).fetchone()[0],before+1)

    def test_recovery_code_is_single_use(self):
        u=self.register();self.enable(u);code=u['recovery'][0]
        body=self.challenge(u);self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':code.upper().replace('-',' ')})[0],200)
        self.relax(u['id']);body=self.challenge(u);self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':code})[0],403)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][1]})[0],200)

    def test_smtp_down_fails_closed_but_recovery_code_works(self):
        u=self.register();self.enable(u);self.fail_mail=True
        status,body,cookie=self.login(u);self.assertEqual(status,200);self.assertTrue(body['twofa']);self.assertTrue(body['mailFailed']);self.assertIsNone(cookie)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':'123456'})[0],403)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][2]})[0],200)
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],410,'consumed challenge')
        w=self.register();status,body,_=self.request('POST','2fa/setup',{'password':PASSWORD},w['cookie']);self.assertEqual(status,503);self.assertIn('error',body)
        self.assertFalse(self.request('GET','me',cookie=w['cookie'])[1]['twofa'])
        self.fail_mail=False;self.relax(u['id']);body=self.challenge(u);self.relax(u['id']);self.fail_mail=True
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],503)

    def test_smtp_not_configured_returns_503(self):
        u=self.register()
        with mock.patch.dict(os.environ,{'SMTP_HOST':''}),mock.patch.object(app.mailer,'send',REAL_SEND):
            status,body,_=self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])
        self.assertEqual(status,503)

    def test_resend_cooldown_and_new_code_invalidates_old(self):
        u=self.register();self.enable(u);body=self.challenge(u);old=self.outbox[-1]['code']
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],429)
        self.relax(u['id'])
        status,resent,_=self.request('POST','login/2fa/resend',{'challenge':body['challenge'],'lang':'ru'});self.assertEqual((status,resent),(200,{'ok':True,'expiresIn':600}))
        self.assertEqual(self.outbox[-1]['lang'],'ru');new=self.outbox[-1]['code']
        if new!=old: self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':old})[0],403)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':new})[0],200)
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':'nope'})[0],410)

    def test_account_mail_budget_degrades_to_recovery_only(self):
        u=self.register();self.enable(u)
        for _ in range(app.TWOFA_MAILS[0]): self.relax(u['id']);self.challenge(u)
        sent=len(self.outbox);self.relax(u['id']);body=self.challenge(u)
        self.assertEqual(len(self.outbox),sent);self.assertTrue(body.get('mailFailed'))
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][3]})[0],200)

    def test_disable_two_steps_with_code_and_with_recovery(self):
        u=self.register();self.enable(u)
        status,step1,_=self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie']);self.assertEqual(status,200);self.assertIn('challenge',step1);self.assertEqual(self.outbox[-1]['purpose'],'disable')
        code=self.outbox[-1]['code']
        self.assertEqual(self.request('POST','2fa/disable',{'password':'Wrong-password-here!2026','challenge':step1['challenge'],'code':code},u['cookie'])[0],403)
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':step1['challenge'],'code':code},u['cookie'])[1],{'ok':True})
        self.assertFalse(self.request('GET','me',cookie=u['cookie'])[1]['twofa']);self.assertEqual(self.rows('SELECT count(*) FROM twofa_recovery WHERE user_id=?',u['id']),[(0,)])
        status,_,cookie=self.login(u);self.assertEqual(status,200);self.assertTrue(cookie)
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie'])[0],409)
        v=self.register();self.enable(v);self.relax(v['id'])
        step1=self.request('POST','2fa/disable',{'password':PASSWORD},v['cookie'])[1]
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':step1['challenge'],'code':v['recovery'][0]},v['cookie'])[1],{'ok':True})

    def test_deletion_cascades_and_expired_rows_are_purged(self):
        u=self.register();self.enable(u);self.challenge(u)
        self.assertEqual(self.request('DELETE','me',{'password':PASSWORD},u['cookie'])[0],200)
        for table in ['twofa_challenges','twofa_recovery','twofa_log']: self.assertEqual(self.rows(f'SELECT count(*) FROM {table} WHERE user_id=?',u['id']),[(0,)])
        v=self.register();self.enable(v)
        with app.db() as c: c.execute('UPDATE twofa_challenges SET expires=1');c.execute('UPDATE twofa_log SET at=1')
        app.init_db();self.assertEqual(self.rows('SELECT count(*) FROM twofa_challenges'),[(0,)]);self.assertEqual(self.rows('SELECT count(*) FROM twofa_log'),[(0,)])

    def test_export_contains_no_two_factor_secrets(self):
        u=self.register();self.enable(u);self.challenge(u)
        status,data,_=self.request('GET','export',cookie=u['cookie']);text=json.dumps(data);self.assertEqual(status,200)
        for row in self.rows('SELECT token,code FROM twofa_challenges')+self.rows('SELECT code FROM twofa_recovery'):
            for secret in row: self.assertNotIn(secret,text)
        self.assertNotIn('recovery',text.lower());self.assertTrue(data['profile']['twofa'])

    def test_invalid_input_shapes(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        for payload in [{},{'challenge':1,'code':'123456'},{'challenge':body['challenge'],'code':['1']},{'challenge':'x'*500,'code':'1'*6},{'challenge':body['challenge'],'code':'1'*200}]:
            self.assertEqual(self.request('POST','login/2fa',payload)[0],400,payload)
        self.assertEqual(self.request('POST','2fa/setup',{'password':PASSWORD})[0],401)
        self.assertEqual(self.request('POST','2fa/bogus',{},u['cookie'])[0],404)

    # --- audit regressions -------------------------------------------------------------------------------------
    def test_login_racing_enable_cannot_keep_a_password_only_session(self):
        u=self.register();setup=self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])[1];code=self.outbox[-1]['code'];result={}
        real=app.Handler.start_session
        def enable(): result['enable']=self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':code},u['cookie'])
        def racing(handler,c,user):
            # Runs after login decided "no 2FA". Old code: enable completes here and the session is inserted afterwards.
            # New code: login holds the write lock, so enable has to wait until the session exists, then revokes it.
            thread=threading.Thread(target=enable);thread.start();thread.join(1);result['thread']=thread
            return real(handler,c,user)
        with mock.patch.object(app.Handler,'start_session',racing): status,body,cookie=self.login(u)
        result['thread'].join(15);self.assertEqual(result['enable'][0],200)
        self.assertEqual(status,200)
        if cookie: self.assertEqual(self.request('GET','me',cookie=cookie)[0],401,'password-only session survived enable')
        else: self.assertTrue(body['twofa'])
        self.assertEqual(self.rows('SELECT count(*) FROM sessions WHERE user_id=?',u['id']),[(1,)])

    def test_login_for_a_user_deleted_during_password_check_is_401(self):
        u=self.register();real=app.hash_password
        def hashing(password,salt=None):
            out=real(password,salt)
            with app.db() as c: c.execute('DELETE FROM users WHERE id=?',(u['id'],))
            return out
        with mock.patch.object(app,'hash_password',hashing): status,body,cookie=self.login(u)
        self.assertEqual(status,401);self.assertIsNone(cookie)

    def test_wrong_code_and_password_are_403_and_only_a_missing_session_is_401(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':'abcde-fghjk'})[0],403)
        self.assertEqual(self.request('POST','2fa/disable',{'password':'Wrong-password-here!2026'},u['cookie'])[0],403)
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD},None)[0],401)
        self.assertEqual(self.request('GET','me',cookie=u['cookie'])[0],200)

    def test_verification_clears_cooldown_but_keeps_hourly_count(self):
        u=self.register();setup=self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])[1]
        self.assertEqual(self.request('POST','2fa/enable',{'challenge':setup['challenge'],'code':self.outbox[-1]['code']},u['cookie'])[0],200)
        for _ in range(2):  # right after enable, then right after a login: never a 429
            sent=len(self.outbox);body=self.challenge(u);self.assertNotIn('mailFailed',body);self.assertEqual(len(self.outbox),sent+1)
            self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})[0],200)
        self.assertEqual(self.rows("SELECT count(*) FROM twofa_log WHERE user_id=? AND kind='mail'",u['id']),[(3,)])

    def test_login_in_cooldown_returns_unsent_challenge_that_recovery_and_resend_complete(self):
        u=self.register();self.enable(u);first=self.challenge(u);sent=len(self.outbox)
        status,body,cookie=self.login(u);self.assertEqual(status,200);self.assertIsNone(cookie)
        self.assertTrue(body['challenge'] and body['mailFailed']);self.assertTrue(0<body['retryAfter']<=app.TWOFA_COOLDOWN);self.assertNotIn('mailLimited',body);self.assertEqual(len(self.outbox),sent)
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],429)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][0]})[0],200)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':first['challenge'],'code':self.outbox[-1]['code']})[0],200)
        self.relax(u['id']);self.challenge(u);status,body,_=self.login(u);self.assertTrue(body['mailFailed']);self.relax(u['id'])
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],200)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})[0],200)

    def test_disable_step_one_in_cooldown_is_unsent_but_recovery_completes_it(self):
        u=self.register();self.enable(u);self.challenge(u);sent=len(self.outbox)
        status,step1,_=self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie'])
        self.assertEqual(status,200);self.assertTrue(step1['mailFailed']);self.assertTrue(0<step1['retryAfter']<=app.TWOFA_COOLDOWN);self.assertEqual(len(self.outbox),sent)
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':step1['challenge'],'code':u['recovery'][0]},u['cookie'])[1],{'ok':True})

    def test_setup_in_cooldown_is_429_with_retry_after(self):
        u=self.register();self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])
        status,body,_=self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])
        self.assertEqual(status,429);self.assertTrue(0<body['retryAfter']<=app.TWOFA_COOLDOWN);self.assertNotIn('уже отправлен',body['error'])

    def test_unsent_challenge_has_unguessable_digest(self):
        u=self.register();self.enable(u);self.fail_mail=True
        with mock.patch.object(app,'twofa_new_code',lambda:'123456'):
            body=self.login(u)[1];self.assertTrue(body['mailFailed'])  # delivery failed
            status,cooling,_=self.login(u);self.assertTrue(cooling['mailFailed'])
        for challenge in (body,cooling):
            digest=self.rows('SELECT code FROM twofa_challenges WHERE token=?',app.mac('tok',challenge['challenge']))[0][0]
            self.assertNotEqual(digest,app.mac('code',app.mac('tok',challenge['challenge']),'123456'))
            self.assertEqual(self.request('POST','login/2fa',{'challenge':challenge['challenge'],'code':'123456'})[0],403)

    def test_non_ascii_token_and_code_are_400_and_cost_no_attempt(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        for payload in [{'challenge':body['challenge'],'code':'12345\u0663'},{'challenge':'\u00e9'+body['challenge'],'code':'123456'},{'challenge':body['challenge'],'code':'\u00e9\u00e9\u00e9\u00e9'}]:
            self.assertEqual(self.request('POST','login/2fa',payload)[0],400,payload)
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':'\u00e9'})[0],400)
        self.assertEqual(self.rows('SELECT attempts FROM twofa_challenges WHERE token=?',app.mac('tok',body['challenge'])),[(0,)])

    def test_lockout_counts_only_failed_digit_codes_and_never_burns_or_extends_while_locked(self):
        u=self.register();self.enable(u)
        for _ in range(2):
            body=self.challenge(u);bad='000000' if self.outbox[-1]['code']!='000000' else '000001';self.relax(u['id'])
            for _ in range(5): self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':bad})[0],403)
        tries=self.rows("SELECT at FROM twofa_log WHERE user_id=? AND kind='try' ORDER BY at",u['id']);self.assertEqual(len(tries),app.TWOFA_TRIES[0])
        body=self.challenge(u);good=self.outbox[-1]['code']
        for _ in range(3): self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':good})[0],429)
        self.assertEqual(self.rows("SELECT at FROM twofa_log WHERE user_id=? AND kind='try' ORDER BY at",u['id']),tries,'locked requests must not extend the lock')
        self.assertEqual(self.rows('SELECT attempts FROM twofa_challenges WHERE token=?',app.mac('tok',body['challenge'])),[(0,)])
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][0]})[0],200,'recovery codes stay usable while locked')
        self.assertEqual(len(self.rows("SELECT at FROM twofa_log WHERE user_id=? AND kind='try'",u['id'])),app.TWOFA_TRIES[0])

    def test_recovery_failures_and_successes_do_not_spend_the_digit_budget(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        for _ in range(2): self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':'abcde-fghjk'})[0],403)
        self.assertEqual(self.rows('SELECT attempts FROM twofa_challenges WHERE token=?',app.mac('tok',body['challenge'])),[(2,)])
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})[0],200)
        self.assertEqual(self.rows("SELECT count(*) FROM twofa_log WHERE user_id=? AND kind='try'",u['id']),[(0,)])

    def test_failed_resend_keeps_the_previous_code_working(self):
        u=self.register();self.enable(u);body=self.challenge(u);old=self.outbox[-1]['code'];self.relax(u['id']);self.fail_mail=True
        status,failed,_=self.request('POST','login/2fa/resend',{'challenge':body['challenge']})
        self.assertEqual(status,503);self.assertTrue(0<failed['retryAfter']<=app.TWOFA_COOLDOWN)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':old})[0],200)

    def test_concurrent_resends_send_one_mail_and_the_delivered_code_works(self):
        u=self.register();self.enable(u);body=self.challenge(u);self.relax(u['id']);sent=len(self.outbox)
        with concurrent.futures.ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda _:self.request('POST','login/2fa/resend',{'challenge':body['challenge']}),range(2)))
        self.assertEqual(sorted(r[0] for r in results),[200,429]);self.assertTrue(next(r for r in results if r[0]==429)[1]['retryAfter']>0)
        self.assertEqual(len(self.outbox),sent+1);self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':self.outbox[-1]['code']})[0],200)

    def test_resend_cannot_revive_a_challenge_consumed_during_delivery(self):
        u=self.register();self.enable(u);body=self.challenge(u);self.relax(u['id']);seen={}
        def consuming(to,code,lang='en',purpose='login',minutes=10):
            seen['login']=self.request('POST','login/2fa',{'challenge':body['challenge'],'code':u['recovery'][0]})[0]
        with mock.patch.object(app.mailer,'send',consuming): status,_,_=self.request('POST','login/2fa/resend',{'challenge':body['challenge']})
        self.assertEqual((seen['login'],status),(200,410))

    def test_dead_challenge_is_410_for_every_reason(self):
        u=self.register();self.enable(u);body=self.challenge(u)
        self.assertEqual(self.request('POST','login/2fa',{'challenge':'unknown','code':'123456'})[0],410)
        with app.db() as c: c.execute('UPDATE twofa_challenges SET expires=? WHERE user_id=?',(app.now()-1,u['id']))
        self.assertEqual(self.request('POST','login/2fa',{'challenge':body['challenge'],'code':'123456'})[0],410)
        self.assertEqual(self.request('POST','login/2fa/resend',{'challenge':body['challenge']})[0],410)

    def test_enable_rechecks_state_under_lock_and_drops_other_challenges(self):
        u=self.register();first=self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie'])[1];code=self.outbox[-1]['code'];self.relax(u['id'])
        self.request('POST','2fa/setup',{'password':PASSWORD},u['cookie']);self.assertEqual(self.rows('SELECT count(*) FROM twofa_challenges WHERE user_id=?',u['id']),[(2,)])
        real=app.twofa_check
        def enabled_meanwhile(*args,**kwargs):  # another request enables 2FA after this one read the account
            with app.db() as c: c.execute('UPDATE users SET twofa=1 WHERE id=?',(u['id'],))
            return real(*args,**kwargs)
        with mock.patch.object(app,'twofa_check',enabled_meanwhile): status=self.request('POST','2fa/enable',{'challenge':first['challenge'],'code':code},u['cookie'])[0]
        self.assertEqual(status,409);self.assertEqual(self.rows('SELECT count(*) FROM twofa_recovery WHERE user_id=?',u['id']),[(0,)])
        self.assertEqual(self.rows('SELECT used FROM twofa_challenges WHERE token=?',app.mac('tok',first['challenge'])),[(0,)])
        with app.db() as c: c.execute('UPDATE users SET twofa=0 WHERE id=?',(u['id'],))
        self.assertEqual(self.request('POST','2fa/enable',{'challenge':first['challenge'],'code':code},u['cookie'])[0],200)
        self.assertEqual(self.rows('SELECT count(*) FROM twofa_challenges WHERE user_id=?',u['id']),[(0,)])

    def test_password_budget_counts_step_one_only(self):
        u=self.register();self.enable(u);step1=None  # enable's setup already used one of the five
        for i in range(4):
            status,body,_=self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie']);self.assertEqual(status,200)
            if i==0: step1,code=body,self.outbox[-1]['code']
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie'])[0],429)
        self.assertEqual(self.request('POST','2fa/disable',{'password':PASSWORD,'challenge':step1['challenge'],'code':code},u['cookie'])[1],{'ok':True})

    def test_step_two_password_guessing_is_still_bounded(self):
        u=self.register();self.enable(u);step1=self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie'])[1]
        statuses=[self.request('POST','2fa/disable',{'password':'Wrong-password-here!2026','challenge':step1['challenge'],'code':'123456'},u['cookie'])[0] for _ in range(12)]
        self.assertEqual(statuses[:10],[403]*10);self.assertEqual(statuses[10:],[429]*2)

    def test_hourly_mail_budget_has_a_clear_message(self):
        u=self.register();self.enable(u)
        for _ in range(app.TWOFA_MAILS[0]-1): self.relax(u['id']);self.challenge(u)  # enable's setup mail was the sixth
        self.relax(u['id']);status,body,_=self.request('POST','2fa/disable',{'password':PASSWORD},u['cookie'])
        self.assertEqual(status,200);self.assertTrue(body['mailFailed'] and body['mailLimited']);self.assertNotIn('retryAfter',body)
        w=self.register()
        with app.db() as c: c.executemany("INSERT INTO twofa_log VALUES(?,'mail',?)",[(w['id'],app.now()-100)]*app.TWOFA_MAILS[0])
        status,body,_=self.request('POST','2fa/setup',{'password':PASSWORD},w['cookie']);self.assertEqual((status,body['error']),(429,app.MAIL_LIMIT));self.assertIn('Лимит',body['error'])

class KeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        patcher=mock.patch.object(app,'DB',self.root/'x.sqlite3');patcher.start();self.addCleanup(patcher.stop)
        env=mock.patch.dict(os.environ,{'TWOFA_SECRET':''});env.start();self.addCleanup(env.stop)
    def test_empty_or_short_key_file_is_refused_not_used(self):
        (self.root/'twofa.key').write_bytes(b'')
        with self.assertRaises(RuntimeError): app.twofa_key()
        (self.root/'twofa.key').write_text('abcd')
        with self.assertRaises(RuntimeError): app.twofa_key()
    def test_key_is_created_complete_with_mode_0600_and_no_temp_left(self):
        key=app.twofa_key();self.assertEqual(len(key),32);self.assertEqual(oct(os.stat(self.root/'twofa.key').st_mode&0o777),'0o600')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['twofa.key'])
    def test_failed_write_leaves_no_final_file_and_next_call_succeeds(self):
        class Broken:
            def __enter__(self): return self
            def __exit__(self,*a): pass
            def write(self,x): raise OSError(28,'No space left on device')
        with mock.patch.object(app.os,'fdopen',lambda fd,mode:(os.close(fd),Broken())[1]):
            with self.assertRaises(OSError): app.twofa_key()
        self.assertEqual(list(self.root.iterdir()),[]);self.assertEqual(len(app.twofa_key()),32)
    def test_racing_creators_agree_on_one_key(self):
        with concurrent.futures.ThreadPoolExecutor(8) as pool: keys=set(pool.map(lambda _:(app.KEYS.clear(),app.twofa_key())[1],range(8)))
        self.assertEqual(len(keys),1)
    def test_env_secret_needs_32_characters(self):
        with mock.patch.dict(os.environ,{'TWOFA_SECRET':'short'}),self.assertRaises(RuntimeError): app.twofa_key()
        with mock.patch.dict(os.environ,{'TWOFA_SECRET':'x'*32}): self.assertEqual(app.twofa_key(),b'x'*32)

class MigrationTests(unittest.TestCase):
    def test_existing_database_gains_column_and_keeps_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'old.sqlite3'
            with sqlite3.connect(path) as c:
                c.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password TEXT NOT NULL, name TEXT NOT NULL, bio TEXT NOT NULL DEFAULT "", avatar BLOB, created INTEGER NOT NULL, seen INTEGER NOT NULL)')
                c.execute("INSERT INTO users(email,password,name,created,seen) VALUES('old@example.test','salt:hash','Old',1,1)")
            previous=app.DB;app.DB=path
            try:
                app.init_db();app.init_db()
                with app.db() as c:
                    self.assertEqual(tuple(c.execute('SELECT name,twofa FROM users').fetchone()),('Old',0))
                    self.assertIn('twofa_challenges',{r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            finally: app.DB=previous

class FakeSmtp(socketserver.StreamRequestHandler):
    def handle(self):
        w=lambda s:self.wfile.write((s+'\r\n').encode())
        if getattr(self.server,'stall',False): time.sleep(3);return  # never greets
        w('220 fake');data=False;lines=[]
        for raw in self.rfile:
            line=raw.decode().rstrip('\r\n')
            if data:
                if line=='.': data=False;self.server.messages.append('\n'.join(lines));w('250 queued')
                else: lines.append(line)
            elif line.upper().startswith('EHLO'): w('250-fake');w('250 8BITMIME')
            elif line.upper().startswith('DATA'): data=True;lines=[];w('354 go')
            elif line.upper().startswith('QUIT'): w('221 bye');return
            else: self.server.commands.append(line);w('250 ok')

class MailerTests(unittest.TestCase):
    def setUp(self):
        self.server=socketserver.ThreadingTCPServer(('127.0.0.1',0),FakeSmtp);self.server.messages=[];self.server.commands=[]
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        env={'SMTP_HOST':'127.0.0.1','SMTP_PORT':str(self.server.server_address[1]),'SMTP_TLS':'none','SMTP_USER':'user','SMTP_PASSWORD':'secret-pass'}
        patcher=mock.patch.dict(os.environ,env);patcher.start();self.addCleanup(patcher.stop)
        self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close()))
    def test_delivers_multipart_localized_mail_without_credentials(self):
        mailer.send('per@example.test','123456','es','enable')
        message=self.server.messages[0];self.assertIn('123456',message);self.assertIn('text/html',message);self.assertIn('text/plain',message);self.assertIn('verificación',email.message_from_string(message,policy=email.policy.default)['Subject'])
        self.assertFalse(any(c.upper().startswith('AUTH') for c in self.server.commands));self.assertNotIn('secret-pass',message)
    def test_header_injection_is_rejected_and_nothing_is_sent(self):
        with self.assertRaises(mailer.MailError): mailer.send('a@b.test\r\nBcc: evil@x.test','123456')
        self.assertEqual(self.server.messages,[])
    def test_only_one_plain_recipient_is_accepted(self):
        for to in ['a@b.test, c@d.test','a@b.test;c@d.test','a,b@c.test','Eve <a@b.test>','']:
            with self.assertRaises(mailer.MailError,msg=to): mailer.send(to,'123456')
        self.assertEqual(self.server.messages,[]);mailer.send('ok@b.test','123456');self.assertEqual(len(self.server.messages),1)
    def test_total_deadline_cuts_a_stalling_server(self):
        self.server.stall=True
        with mock.patch.object(mailer,'DEADLINE',0.5),mock.patch.object(mailer,'TIMEOUT',5):
            started=time.monotonic()
            with self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')
        self.assertLess(time.monotonic()-started,3)
    def test_failures_become_mail_errors(self):
        with mock.patch.dict(os.environ,{'SMTP_HOST':''}),self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')
        with mock.patch.dict(os.environ,{'SMTP_TLS':'starttls'}),self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')  # server lacks STARTTLS: no downgrade
        with mock.patch.dict(os.environ,{'SMTP_TLS':'bogus'}),self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')
        with mock.patch.dict(os.environ,{'SMTP_PORT':'1'}),self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')
        self.assertEqual(self.server.messages,[])

class MailerTlsTests(unittest.TestCase):
    """Real TLS handshakes: the certificate must match the SMTP host and be signed by SMTP_CA."""
    def serve(self,mode,names):
        import socket
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);root=Path(tmp.name)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','1','-subj','/CN=x','-addext','subjectAltName='+names,'-keyout',str(root/'k.pem'),'-out',str(root/'c.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'c.pem',root/'k.pem')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(10);self.addCleanup(listener.close)
        received=[]
        def run():
            try:
                s,_=listener.accept();s.settimeout(10)
                if mode=='ssl': s=context.wrap_socket(s,server_side=True)
                f=s.makefile('rb');secure=mode=='ssl';s.sendall(b'220 x\r\n')
                while True:
                    line=f.readline();verb=line.split()[0].upper() if line.strip() else b''
                    if not line: return
                    if verb==b'EHLO': s.sendall(b'250 x\r\n' if secure else b'250-x\r\n250 STARTTLS\r\n')
                    elif verb==b'STARTTLS':
                        s.sendall(b'220 go\r\n');s=context.wrap_socket(s,server_side=True);f=s.makefile('rb');secure=True
                    elif verb==b'DATA':
                        s.sendall(b'354 go\r\n');body=b''
                        while not body.endswith(b'\r\n.\r\n'): body+=f.readline()
                        received.append((secure,body));s.sendall(b'250 ok\r\n')
                    elif verb==b'QUIT': s.sendall(b'221 bye\r\n');return
                    else: s.sendall(b'250 ok\r\n')
            except OSError: pass
        thread=threading.Thread(target=run,daemon=True);thread.start();self.addCleanup(thread.join,10)
        env={'SMTP_HOST':'localhost','SMTP_PORT':str(listener.getsockname()[1]),'SMTP_TLS':mode,'SMTP_CA':str(root/'c.pem'),'SMTP_USER':''}
        return env,received
    def test_verified_tls_delivers(self):
        for mode in ['starttls','ssl']:
            with self.subTest(mode):
                env,received=self.serve(mode,'DNS:localhost')
                with mock.patch.dict('os.environ',env): mailer.send('a@b.test','123456')
                self.assertEqual(len(received),1);self.assertTrue(received[0][0]);self.assertIn(b'123456',received[0][1])
    def test_notice_is_delivered_over_tls(self):
        for kind in ['export','erasure']:
            for lang in mailer.LANGS:
                env,received=self.serve('starttls','DNS:localhost')
                with mock.patch.dict('os.environ',env): mailer.notify('a@b.test',kind,lang)
                self.assertEqual(len(received),1)
    def test_wrong_hostname_is_refused(self):
        for mode in ['starttls','ssl']:
            with self.subTest(mode):
                env,received=self.serve(mode,'DNS:other.test')
                with mock.patch.dict('os.environ',env),self.assertRaises(mailer.MailError): mailer.send('a@b.test','123456')
                self.assertEqual(received,[])

if __name__=='__main__': unittest.main()
