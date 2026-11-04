import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from server.recovery import snapshot, restore_copy, BackupService

class RecoveryTests(unittest.TestCase):
    def test_snapshot_restores_committed_wal_and_relationships_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=root/'live.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE saves(user_id INTEGER REFERENCES users(id),payload TEXT);INSERT INTO users VALUES(1,"Mara");INSERT INTO saves VALUES(1,"case-4");')
                first=snapshot(db,root/'backups',2)
                c.execute('INSERT INTO users VALUES(2,"Yuna")');c.commit()
                second=snapshot(db,root/'backups',2)
                rows=restore_copy(second,root/'restored.sqlite3')
                self.assertEqual(rows,{'users':2,'saves':1})
                with self.assertRaises(FileExistsError):restore_copy(first,db)
                with sqlite3.connect(root/'restored.sqlite3') as recovered:
                    self.assertEqual(recovered.execute('SELECT payload FROM saves WHERE user_id=1').fetchone()[0],'case-4')
                keep=root/'backups'/'manual.sqlite3';keep.write_text('do not remove')
                snapshot(db,root/'backups',2)
                self.assertEqual(len(list((root/'backups').glob('snapshot-*.sqlite3'))),2)
                self.assertEqual(keep.read_text(),'do not remove')
                self.assertEqual(os.stat(second).st_mode & 0o777,0o600)
    def test_corruption_and_backup_failure_are_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);bad=root/'corrupt.sqlite3';bad.write_bytes(b'not a database')
            with self.assertRaises(sqlite3.DatabaseError):restore_copy(bad,root/'restore.sqlite3')
            self.assertFalse((root/'restore.sqlite3').exists())
            service=BackupService(root/'missing.sqlite3',root/'copies')
            self.assertIsNone(service.run_once());self.assertEqual(service.status()['status'],'degraded')
            with sqlite3.connect(root/'missing.sqlite3') as c:c.execute('CREATE TABLE example(id INTEGER)')
            self.assertIsNotNone(service.run_once());self.assertEqual(service.status()['status'],'ok')
            service.last_success=0;self.assertEqual(service.status()['status'],'degraded')
