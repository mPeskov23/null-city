"""Native HTTPS launcher for environments where Docker execution is unavailable."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
settings = ROOT / '.env'
allowed = {
    'PUBLIC_ORIGIN', 'PORT', 'BIND', 'DATABASE_PATH',
    'TLS_CERT', 'TLS_KEY', 'BACKUP_INTERVAL_SECONDS', 'BACKUP_RETAIN',
    'HOST_IP', 'ALLOWED_ORIGINS'
}
if settings.exists():
    for line in settings.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() in allowed:
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))

from scripts.generate_cert import main as setup_cert
setup_cert()

print('Оставь этот терминал открытым. Остановка сервера: Ctrl+C.\n', flush=True)
runpy.run_path(str(ROOT / 'server/app.py'), run_name='__main__')
