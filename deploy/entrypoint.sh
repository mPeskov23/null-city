#!/bin/sh
set -eu
umask 077
mkdir -p /app/data/tls
if [ -f scripts/generate_cert.py ]; then
  python3 scripts/generate_cert.py || true
fi
if [ ! -f /app/data/tls/cert.pem ]; then
  SAN="DNS:localhost,IP:127.0.0.1,IP:0.0.0.0"
  if [ -n "${HOST_IP:-}" ]; then
    SAN="${SAN},IP:${HOST_IP}"
  fi
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 -subj '/CN=localhost' -addext "subjectAltName=${SAN}" -keyout /app/data/tls/key.pem -out /app/data/tls/cert.pem >/dev/null 2>&1
fi
exec python3 server/app.py
