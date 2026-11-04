"""Smoke test the real native launcher, TLS setup and backup scheduler together."""
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import unittest
import urllib.request
import urllib.error

class LauncherTests(unittest.TestCase):
    def test_launcher_serves_tls_and_creates_verified_backup(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp)
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            env={**os.environ,'PORT':str(port),'BIND':'127.0.0.1','PUBLIC_ORIGIN':f'https://localhost:{port}',
                 'DATABASE_PATH':str(work/'app.sqlite3'),'TLS_CERT':str(work/'cert.pem'),'TLS_KEY':str(work/'key.pem'),
                 'BACKUP_INTERVAL_SECONDS':'60','BACKUP_RETAIN':'2'}
            process=subprocess.Popen(['python3',str(root/'scripts/serve.py')],cwd=temp,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                result=None
                for _ in range(100):
                    if process.poll() is not None:self.fail(process.stdout.read().decode())
                    if (work/'cert.pem').exists():
                        try:
                            context=ssl.create_default_context(cafile=str(work/'cert.pem'))
                            with urllib.request.urlopen(f'https://localhost:{port}/api/status',context=context,timeout=1) as r:result=json.load(r)
                            if result['backups']['status']=='ok':break
                        except (OSError,urllib.error.URLError,ssl.SSLError):pass
                    time.sleep(.1)
                self.assertIsNotNone(result,'The launcher did not become ready')
                self.assertEqual(result['status'],'ok');self.assertEqual(result['backups']['retainedCopies'],2)
                self.assertTrue(list((work/'automatic-backups').glob('snapshot-*.sqlite3')))
            finally:
                process.terminate()
                try:process.communicate(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.communicate()
