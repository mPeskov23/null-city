import concurrent.futures
import base64
import io
from PIL import Image
import http.client
import importlib.util
import json
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import app

class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();root=Path(cls.tmp.name);app.DB=root/'db.sqlite3';app.init_db()
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);cls.port=cls.server.server_address[1];app.ORIGIN=f'https://localhost:{cls.port}'
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem');cls.server.socket=context.wrap_socket(cls.server.socket,server_side=True)
        cls.context=ssl.create_default_context(cafile=str(root/'cert.pem'))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.users=[]
        for i in range(4):
            status,data,cookie=cls.request('POST','register',{'email':f'test{i}@example.test','name':f'Operator {i}','password':'Test-only-passphrase!2026'})
            assert status==200,(status,data)
            cls.users.append((data,cookie))
    def setUp(self):
        with app.RATE_LOCK: app.RATES.clear()

    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()
    @classmethod
    def request(cls,method,path,data=None,cookie=None,origin=None,host=None):
        c=http.client.HTTPSConnection('localhost',cls.port,context=cls.context,timeout=10)
        headers={'Origin':origin or app.ORIGIN,'X-NullCity':'1','Content-Type':'application/json'}
        if host: headers['Host']=host
        if cookie: headers['Cookie']=cookie
        c.request(method,'/api/'+path,json.dumps(data) if data is not None else None,headers);r=c.getresponse();body=json.loads(r.read());token=r.getheader('Set-Cookie');status=r.status;c.close();return status,body,token.split(';')[0] if token else None
    def test_registration_validation_duplicate_and_session_lifecycle(self):
        credentials={'email':' Session@Test.Invalid ', 'name':'Session tester', 'password':'Session-test-password!2026'}
        for invalid in [{'email':'broken'}, {'password':'short'}, {'name':'x'}]:
            self.assertEqual(self.request('POST','register',credentials|invalid)[0],400)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: self.request('POST','register',credentials),range(2)))
        self.assertEqual(sorted(r[0] for r in results),[200,409])
        first=next(r for r in results if r[0]==200)
        self.assertEqual(first[1]['email'],'session@test.invalid')
        bad=self.request('POST','login',credentials|{'password':'Incorrect-password!2026'})
        absent=self.request('POST','login',credentials|{'email':'absent@test.invalid'})
        self.assertEqual((bad[0],bad[1]),(absent[0],absent[1]))
        self.assertEqual(bad[0],401)
        status,_,second=self.request('POST','login',credentials)
        self.assertEqual(status,200);self.assertNotEqual(first[2],second)
        status,_,cleared=self.request('POST','logout',{},second)
        self.assertEqual(status,200);self.assertEqual(cleared,'__Host-nullcity=')
        self.assertEqual(self.request('GET','me',cookie=second)[0],401)
        self.assertEqual(self.request('GET','me',cookie=first[2])[0],200)
        with app.db() as c: c.execute('UPDATE sessions SET expires=? WHERE user_id=?',(app.now()-1,first[1]['id']))
        self.assertEqual(self.request('GET','me',cookie=first[2])[0],401)

    def test_stale_round_and_spectator_actions(self):
        a,b,spectator=[u[1] for u in self.users[:3]]
        status,created,_=self.request('POST','rooms',{'name':'Deadline check','capacity':2,'topic':'mixed','duration':15},a)
        self.assertEqual(status,200);rid=created['id'];path=f'rooms/{rid}'
        self.assertEqual(self.request('POST',path+'/ready',{'ready':True},spectator)[0],403)
        self.request('POST',path+'/join',{},b)
        for cookie in [a,b]: self.request('POST',path+'/ready',{'ready':True},cookie)
        self.assertEqual(self.request('POST',path+'/start',{},a)[0],200)
        self.assertEqual(self.request('POST',path+'/hint',{'round':0},spectator)[0],403)
        self.request('POST',path+'/hint',{'round':0},a)
        watching=self.request('GET',path,cookie=spectator)[1]
        self.assertIsNone(watching['eliminated']);self.assertFalse(watching['hintUsed'])
        self.assertNotIn('review',watching);self.assertNotIn('answer',watching['question'])
        with app.db() as c: c.execute('UPDATE rooms SET deadline=? WHERE id=?',(app.now()-1,rid))
        self.assertEqual(self.request('POST',path+'/answer',{'round':0,'choice':0},a)[0],409)
        reconnected=self.request('GET',path,cookie=b)[1]
        self.assertEqual(reconnected['round'],1)
        self.assertTrue(all(p['score']==0 for p in reconnected['players']))
        self.assertEqual(self.request('POST',path+'/answer',{'round':0,'choice':0},b)[0],409)
        self.assertEqual(self.request('POST',path+'/answer',{'round':1,'choice':0},a)[0],200)
        self.assertEqual(self.request('POST',path+'/answer',{'round':1,'choice':0},a)[0],409)

    def test_status_reports_verified_backups_and_static_locales(self):
        from server.recovery import BackupService
        previous=app.BACKUPS
        try:
            app.BACKUPS=BackupService(app.DB,Path(self.tmp.name)/'scheduled')
            self.assertEqual(self.request('GET','status')[0],503)
            self.assertIsNotNone(app.BACKUPS.run_once())
            status,body,_=self.request('GET','status')
            self.assertEqual(status,200)
            self.assertEqual(body['database'],'ok')
            self.assertEqual(body['backups']['status'],'ok')
            self.assertNotIn(str(app.DB),json.dumps(body))
            for path in ['/status.html','/src/i18n.js','/src/i18n-catalog.js','/src/i18n.css','/src/status.js','/assets/signal-lost/opening-cover.png']:
                conn=http.client.HTTPSConnection('localhost',self.port,context=self.context,timeout=10)
                conn.request('GET',path);response=conn.getresponse()
                self.assertEqual(response.status,200,path);self.assertTrue(response.read());conn.close()
        finally: app.BACKUPS=previous

    def test_auth_and_csrf(self):
        self.assertEqual(self.request('GET','me')[0],401)
        status,me,_=self.request('GET','me',cookie=self.users[0][1]);self.assertEqual(status,200);self.assertNotIn('password',me)
        self.assertEqual(self.request('PUT','me',{'name':'Changed','bio':''},self.users[0][1],origin='https://evil.invalid')[0],403)
        lan_host = '192.168.1.100:8443'
        self.assertEqual(self.request('PUT','me',{'name':'Changed','bio':''},self.users[0][1],origin=f'https://{lan_host}',host=lan_host)[0],200)
        with app.db() as c:
            stored=c.execute('SELECT password FROM users LIMIT 1').fetchone()[0]
        self.assertNotIn('Test-only',stored)
    def test_save_isolation_and_optimistic_concurrency(self):
        a,b=self.users[0][1],self.users[1][1]
        save={'version':1,'done':[],'xp':0,'spentXp':0,'notes':'private'}
        self.assertEqual(self.request('PUT','save',{'revision':0,'save':save},a)[0],200)
        self.assertIsNone(self.request('GET','save',cookie=b)[1]['save'])
        def update(i): return self.request('PUT','save',{'revision':1,'save':{**save,'notes':str(i)}},a)[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool: statuses=list(pool.map(update,[1,2]))
        self.assertEqual(sorted(statuses),[200,409])
        bad={**save,'appearance':{'skin':'javascript:alert(1)'}}
        self.assertEqual(self.request('PUT','save',{'revision':2,'save':bad},a)[0],400)
        chapter={**save,'done':['first','route','dns','logs','firewall','transfer','report','signal','access','restore','broadcast'],'xp':1850,'story':{'broadcastChoice':'engineers'},'interiorId':'radio'}
        self.assertEqual(self.request('PUT','save',{'revision':2,'save':chapter},a)[0],200)
        loaded=self.request('GET','save',cookie=a)[1]['save']
        self.assertEqual(loaded['story']['broadcastChoice'],'engineers')
        self.assertEqual(len(loaded['done']),11)
    def test_friends_chat_and_profile(self):
        a,b=self.users[0],self.users[1]
        self.assertEqual(self.request('POST','friends',{'userId':b[0]['id']},a[1])[0],200)
        rows=self.request('GET','friends',cookie=b[1])[1];self.assertEqual(rows[0]['accepted'],0)
        self.request('POST','friends',{'userId':a[0]['id']},b[1]);self.assertEqual(self.request('GET','friends',cookie=a[1])[1][0]['accepted'],1)
        self.request('POST','messages',{'body':'Hello, district!'},a[1]);self.assertEqual(self.request('GET','messages',cookie=b[1])[1][-1]['body'],'Hello, district!')
        self.assertEqual(self.request('POST','messages',{'body':'x'*501},a[1])[0],400)
        self.assertEqual(self.request('PUT','avatar',{'image':'not-an-image'},a[1])[0],400)
        image=Image.new('RGB',(16,16),'purple');out=io.BytesIO();image.save(out,format='PNG')
        self.assertEqual(self.request('PUT','avatar',{'image':base64.b64encode(out.getvalue()).decode()},a[1])[0],200)
        self.assertTrue(self.request('GET',f"avatar/{a[0]['id']}",cookie=b[1])[1]['image'])
        self.assertEqual(self.request('PUT','me',{'name':'Renamed operator','bio':'Network learner'},a[1])[0],200)
        self.assertEqual(self.request('GET','users?q=Renamed&online=1&sort=name&page=0',cookie=b[1])[1]['users'][0]['id'],a[0]['id'])
        self.assertEqual(self.request('GET','users?page=not-a-number',cookie=b[1])[0],400)
        self.request('DELETE','friends',{'userId':b[0]['id']},a[1]);self.assertEqual(self.request('GET','friends',cookie=a[1])[1],[])
    def test_four_player_game_authority_reconnection_and_spectator(self):
        status,result,_=self.request('POST','rooms',{'name':'Four players','capacity':4,'topic':'mixed','duration':15},self.users[0][1]);self.assertEqual(status,200);rid=result['id']
        for user,cookie in self.users[1:]: self.assertEqual(self.request('POST',f'rooms/{rid}/join',{},cookie)[0],200)
        self.assertEqual(self.request('POST',f'rooms/{rid}/start',{},self.users[1][1])[0],403)
        self.assertEqual(self.request('POST',f'rooms/{rid}/start',{},self.users[0][1])[0],409)
        for user,cookie in self.users: self.request('POST',f'rooms/{rid}/ready',{'ready':True},cookie)
        self.request('POST',f'rooms/{rid}/start',{},self.users[0][1])
        for roundno in range(5):
            view=self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]
            self.assertNotIn('answer',view['question']);self.assertEqual(view['round'],roundno)
            with app.db() as c:
                deck=json.loads(c.execute('SELECT deck FROM rooms WHERE id=?',(rid,)).fetchone()[0])
            correct=app.QUESTIONS[deck[roundno]]['answer']
            if roundno==0:
                self.assertEqual(self.request('POST',f'rooms/{rid}/hint',{'round':roundno},self.users[0][1])[0],200)
                self.assertEqual(self.request('POST',f'rooms/{rid}/hint',{'round':roundno},self.users[0][1])[0],409)
                hinted=self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]
                self.assertNotEqual(hinted['eliminated'],correct)
                self.assertIsNone(self.request('GET',f'rooms/{rid}',cookie=self.users[1][1])[1]['eliminated'])
            self.request('POST',f'rooms/{rid}/answer',{'round':roundno,'choice':correct,'score':999},self.users[0][1])
            self.assertEqual(self.request('POST',f'rooms/{rid}/answer',{'round':roundno,'choice':correct},self.users[0][1])[0],409)
            # Reconnect is simply a fresh snapshot; prior accepted answer survives.
            self.assertEqual(self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]['choice'],correct)
            for user,cookie in self.users[1:]: self.request('POST',f'rooms/{rid}/answer',{'round':roundno,'choice':(correct+1)%3},cookie)
        final=self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]
        self.assertEqual(final['state'],'finished');self.assertEqual(final['players'][0]['score'],5);self.assertEqual(final['winners'],[self.users[0][0]['id']]);self.assertEqual(len(final['review']),5)
        stats=self.request('GET','stats',cookie=self.users[0][1])[1];self.assertEqual(stats['xp'],140);self.assertTrue(stats['daily']['complete']);self.assertEqual(self.request('GET','stats',cookie=self.users[0][1])[1]['xp'],140);self.assertIn('Победитель смены',stats['badges'])
        # Separate two-player room leaves two real spectators.
        rid=self.request('POST','rooms',{'name':'Spectator','capacity':2,'topic':'network','duration':15},self.users[0][1])[1]['id']
        self.request('POST',f'rooms/{rid}/join',{},self.users[1][1])
        for user,cookie in self.users[:2]: self.request('POST',f'rooms/{rid}/ready',{'ready':True},cookie)
        self.request('POST',f'rooms/{rid}/start',{},self.users[0][1])
        self.assertEqual(self.request('POST',f'rooms/{rid}/answer',{'round':0,'choice':0},self.users[2][1])[0],403)
        with app.db() as c: c.execute('UPDATE rooms SET deadline=0 WHERE id=?',(rid,))
        view=self.request('GET',f'rooms/{rid}',cookie=self.users[2][1])[1];self.assertEqual(view['round'],1)
    def test_live_events_and_concurrent_room_capacity(self):
        c=http.client.HTTPSConnection('localhost',self.port,context=self.context,timeout=10)
        c.request('GET','/api/events',headers={'Cookie':self.users[0][1]})
        stream=c.getresponse();self.assertEqual(stream.status,200)
        first=stream.readline();stream.readline()
        self.request('POST','messages',{'body':'Live event test'},self.users[1][1])
        second=stream.readline();self.assertNotEqual(first,second);c.close()
        rid=self.request('POST','rooms',{'name':'Capacity test','capacity':2,'topic':'mixed','duration':30},self.users[0][1])[1]['id']
        def join(cookie): return self.request('POST',f'rooms/{rid}/join',{},cookie)[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool: statuses=list(pool.map(join,[self.users[1][1],self.users[2][1]]))
        self.assertEqual(sorted(statuses),[200,409])

    def test_lobby_readiness_leave_host_transfer_and_rematch(self):
        a,b,cookie=self.users[0][1],self.users[1][1],self.users[2][1]
        rid=self.request('POST','rooms',{'name':'Lobby flow','capacity':4,'topic':'mixed','duration':30},a)[1]['id']
        self.request('POST',f'rooms/{rid}/join',{},b)
        self.assertEqual(self.request('POST',f'rooms/{rid}/ready',{'ready':True},cookie)[0],403)
        self.assertEqual(self.request('POST',f'rooms/{rid}/ready',{'ready':'yes'},a)[0],400)
        self.assertEqual(self.request('POST',f'rooms/{rid}/start',{},a)[0],409)
        self.request('POST',f'rooms/{rid}/ready',{'ready':True},a)
        self.request('POST',f'rooms/{rid}/ready',{'ready':True},b)
        self.request('POST',f'rooms/{rid}/leave',{},a)
        view=self.request('GET',f'rooms/{rid}',cookie=b)[1]
        self.assertEqual(view['host'],self.users[1][0]['id']);self.assertEqual(len(view['players']),1)
        self.request('POST',f'rooms/{rid}/join',{},a)
        self.assertEqual(self.request('POST',f'rooms/{rid}/start',{},b)[0],409)
        self.request('POST',f'rooms/{rid}/ready',{'ready':True},a)
        self.assertEqual(self.request('POST',f'rooms/{rid}/start',{},b)[0],200)
        self.assertEqual(self.request('POST',f'rooms/{rid}/leave',{},a)[0],409)
        with app.db() as db: db.execute("UPDATE rooms SET state='finished',round=5,deadline=? WHERE id=?",(app.now(),rid))
        self.assertEqual(self.request('POST',f'rooms/{rid}/rematch',{},cookie)[0],403)
        with concurrent.futures.ThreadPoolExecutor(2) as pool: results=list(pool.map(lambda token:self.request('POST',f'rooms/{rid}/rematch',{},token),[a,b]))
        self.assertEqual([r[0] for r in results],[200,200]);self.assertEqual(results[0][1]['id'],results[1][1]['id'])
        new=self.request('GET',f"rooms/{results[0][1]['id']}",cookie=a)[1]
        self.assertEqual(len(new['players']),2);self.assertTrue(all(p['ready']==0 for p in new['players']))

    def test_matchmaking_is_idempotent_and_capacity_safe(self):
        # Use the test-only database; isolate automatic queue from earlier scenarios.
        with app.db() as c: c.execute("UPDATE rooms SET state='cancelled' WHERE state IN ('waiting','playing')")
        with concurrent.futures.ThreadPoolExecutor(4) as pool: results=list(pool.map(lambda pair:self.request('POST','matchmaking',{},pair[1]),self.users))
        self.assertTrue(all(r[0]==200 for r in results));self.assertEqual(len({r[1]['id'] for r in results}),1)
        rid=results[0][1]['id'];view=self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]
        self.assertEqual(len(view['players']),4)
        self.assertEqual(self.request('POST','matchmaking',{},self.users[0][1])[1]['id'],rid)
        app.init_db() # additive migration is safe on an existing populated database
        self.assertEqual(len(self.request('GET',f'rooms/{rid}',cookie=self.users[0][1])[1]['players']),4)

    def test_export_excludes_secrets_and_malformed_input(self):
        status,data,_=self.request('GET','export',cookie=self.users[0][1]);self.assertEqual(status,200);self.assertNotIn('password',json.dumps(data));self.assertNotIn('token',json.dumps(data))
        self.assertEqual(self.request('POST','rooms',{'name':'Bad','capacity':9,'topic':'mixed','duration':30},self.users[0][1])[0],400)

    def test_gdpr_account_deletion_and_data_erasure(self):
        credentials={'email':'gdpr_target@test.invalid','name':'GDPR User','password':'Gdpr-deletion-pass-2026!'}
        status,user,cookie=self.request('POST','register',credentials)
        self.assertEqual(status,200)
        uid=user['id']

        self.request('POST','messages',{'body':'Sensitive message to be erased'},cookie)
        valid_payload={'version':1,'done':['first'],'xp':100,'spentXp':0,'notes':'my personal notes','appearance':{}}
        self.request('PUT','save',{'revision':0,'save':valid_payload},cookie)

        exp_status,exp_data,_=self.request('GET','export',cookie=cookie)
        self.assertEqual(exp_status,200)
        self.assertIn('gdprNotice',exp_data)
        self.assertEqual(exp_data['save']['xp'],100)
        self.assertEqual(len(exp_data['messages']),1)

        wrong_status,wrong_body,_=self.request('DELETE','me',{'password':'Wrong-password-here!2026'},cookie)
        self.assertEqual(wrong_status,401)
        self.assertEqual(wrong_body['error'],'Неверный пароль для подтверждения удаления.')

        self.assertEqual(self.request('GET','me',cookie=cookie)[0],200)

        del_status,del_body,cleared_cookie=self.request('DELETE','me',{'password':'Gdpr-deletion-pass-2026!'},cookie)
        self.assertEqual(del_status,200)
        self.assertTrue(del_body['ok'])
        self.assertEqual(cleared_cookie,'__Host-nullcity=')

        self.assertEqual(self.request('GET','me',cookie=cookie)[0],401)

        with app.db() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM sessions WHERE user_id=?',(uid,)).fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM saves WHERE user_id=?',(uid,)).fetchone())
            self.assertEqual(c.execute('SELECT count(*) FROM messages WHERE user_id=?',(uid,)).fetchone()[0],0)

if __name__=='__main__': unittest.main()
