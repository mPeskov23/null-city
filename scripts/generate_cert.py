#!/usr/bin/env python3
"""Generate a local TLS certificate with SANs covering localhost and LAN IP addresses.
Supports automatic detection of local network IPs and custom HOST_IP/PUBLIC_ORIGIN.
"""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]

def read_env():
    env_file = ROOT / '.env'
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            values[k.strip()] = v.strip().strip('"\'')
    return values

def detect_lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith('127.'):
            ips.add(ip)
    except Exception:
        pass
    try:
        h = socket.gethostname()
        for ip in socket.gethostbyname_ex(h)[2]:
            if ip and not ip.startswith('127.'):
                ips.add(ip)
    except Exception:
        pass
    return ips

def parse_host(raw):
    raw = raw.strip()
    if not raw:
        return None, None
    if '://' in raw:
        raw = urlsplit(raw).netloc
    if ':' in raw:
        raw = raw.split(':')[0]
    try:
        socket.inet_aton(raw)
        return 'IP', raw
    except OSError:
        return 'DNS', raw

def build_sans(extra_hosts=None):
    dns = {'localhost'}
    ips = {'127.0.0.1', '0.0.0.0'}

    try:
        h = socket.gethostname()
        if h:
            dns.add(h)
    except Exception:
        pass

    for ip in detect_lan_ips():
        ips.add(ip)

    for item in (extra_hosts or []):
        for part in item.split(','):
            kind, val = parse_host(part)
            if kind == 'IP':
                ips.add(val)
            elif kind == 'DNS':
                dns.add(val)

    parts = [f'DNS:{d}' for d in sorted(dns)] + [f'IP:{i}' for i in sorted(ips)]
    return ','.join(parts), dns, ips

def cert_has_all_hosts(cert_path, dns, ips):
    if not cert_path.exists():
        return False
    try:
        out = subprocess.check_output(['openssl', 'x509', '-text', '-noout', '-in', str(cert_path)], text=True)
        for d in dns:
            if f'DNS:{d}' not in out and d not in out:
                return False
        for i in ips:
            if f'IP Address:{i}' not in out and f'IP:{i}' not in out:
                return False
        return True
    except Exception:
        return False

def generate_cert(cert_path, key_path, san_string):
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cert_path.parent.chmod(0o700)
    except Exception:
        pass
    old = os.umask(0o077)
    try:
        subprocess.run([
            'openssl', 'req', '-x509',
            '-newkey', 'rsa:3072',
            '-sha256',
            '-nodes',
            '-days', '365',
            '-subj', '/CN=localhost',
            '-addext', f'subjectAltName={san_string}',
            '-keyout', str(key_path),
            '-out', str(cert_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.umask(old)

def main():
    force = '--force' in sys.argv or '--regen' in sys.argv or os.environ.get('REGEN_CERT') == '1'
    cli_hosts = [arg for arg in sys.argv[1:] if not arg.startswith('-')]

    env = read_env()
    cert_path = Path(os.environ.get('TLS_CERT', env.get('TLS_CERT', ROOT / 'data/tls/cert.pem')))
    key_path = Path(os.environ.get('TLS_KEY', env.get('TLS_KEY', ROOT / 'data/tls/key.pem')))

    extra = list(cli_hosts)
    for k in ['HOST_IP', 'PUBLIC_ORIGIN', 'ALLOWED_ORIGINS']:
        val = os.environ.get(k, env.get(k))
        if val:
            extra.append(val)

    san_str, dns, ips = build_sans(extra)

    if force or not cert_path.exists() or not key_path.exists() or not cert_has_all_hosts(cert_path, dns, ips):
        generate_cert(cert_path, key_path, san_str)
        print(f'TLS certificate generated at {cert_path}')
        print(f'  SAN entries: {san_str}')
    else:
        print(f'Using existing valid TLS certificate: {cert_path}')

    port = os.environ.get('PORT', env.get('PORT', '8443'))
    print(f'Server will be accessible at:')
    print(f'  Local: https://localhost:{port}/')
    lan_ips = [i for i in ips if not i.startswith('127.') and i != '0.0.0.0']
    for ip in lan_ips:
        print(f'  LAN:   https://{ip}:{port}/')

if __name__ == '__main__':
    main()
