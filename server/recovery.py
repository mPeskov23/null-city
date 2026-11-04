"""Verified automatic SQLite snapshots and a non-destructive recovery drill."""
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid


def snapshot(source, directory, retain=7):
    source, directory = Path(source), Path(directory)
    if not source.is_file():
        raise FileNotFoundError('Source database does not exist')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'snapshot-' + time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8] + '.sqlite3'
    destination = directory / name
    partial = destination.with_suffix('.partial')
    try:
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(partial) as target:
            src.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
            if target.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Backup relationships are inconsistent')
        with partial.open('rb') as stream:
            os.fsync(stream.fileno())
        partial.rename(destination)
        # Only snapshots owned by this scheduler participate in retention.
        snapshots = sorted(directory.glob('snapshot-*.sqlite3'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in snapshots[max(1, retain):]:
            old.unlink()
        return destination
    finally:
        partial.unlink(missing_ok=True)


def restore_copy(source, destination):
    """Restore to a NEW database; never overwrite the running application's data."""
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise FileNotFoundError('Backup does not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src:
            if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Source backup is corrupt')
            with sqlite3.connect(destination) as target:
                src.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or target.execute('PRAGMA foreign_key_check').fetchone():
                    raise RuntimeError('Restored database failed verification')
                tables=[r[0] for r in target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                return {name:target.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


class BackupService:
    def __init__(self, database, directory, interval=21600, retain=7):
        self.database=Path(database);self.directory=Path(directory)
        self.interval=max(60, int(interval));self.retain=max(1, int(retain))
        self.lock=threading.Lock();self.stop_event=threading.Event()
        self.last_success=None;self.last_failure=None;self.running=False
        self.thread=None
    def run_once(self):
        try:
            destination=snapshot(self.database,self.directory,self.retain)
            with self.lock:
                self.last_success=int(time.time());self.last_failure=None
            return destination
        except Exception:
            with self.lock: self.last_failure=int(time.time())
            return None
    def status(self):
        with self.lock:
            stale=self.last_success is None or time.time()-self.last_success>self.interval*2
            return {'status':'starting' if self.last_success is None and self.last_failure is None else 'degraded' if self.last_failure or stale else 'ok',
                    'lastSuccess':self.last_success,'lastFailure':self.last_failure,'intervalSeconds':self.interval,'retainedCopies':self.retain}
    def start(self):
        if self.thread: return
        def work():
            while not self.stop_event.is_set():
                self.run_once()
                if self.stop_event.wait(self.interval):break
        self.thread=threading.Thread(target=work,name='verified-backups',daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=5)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Restore a verified backup into a NEW database for a recovery drill.')
    parser.add_argument('backup',type=Path);parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    print(json.dumps({'verified':True,'rows':restore_copy(args.backup,args.destination)},indent=2))
