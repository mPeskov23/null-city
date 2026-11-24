#!/bin/sh
# One-shot init for the `smtp` (Mailpit) service. Runs as root with only CAP_CHOWN,
# on a read-only rootfs. Idempotent: does nothing while the certificate is valid
# for more than 30 days.
#
# Writes into two named volumes with DIFFERENT audiences:
#   /pki    (smtp-pki)    -> mounted by Mailpit only: server key, cert, auth file.
#   /shared (smtp-shared) -> mounted read-only by nullcity: PUBLIC CA cert + the
#                            SMTP login (so no secret is committed or put in .env).
# The CA private key lives only in tmpfs (/tmp) and vanishes when this job ends.
set -eu
umask 077
PKI=/pki; SHARED=/shared
MAILPIT_UID=10002; APP_UID=10001

if [ -f "$PKI/cert.pem" ] && [ -f "$PKI/key.pem" ] && [ -f "$PKI/auth.txt" ] \
   && [ -f "$SHARED/ca.pem" ] && [ -f "$SHARED/password" ] \
   && openssl x509 -checkend 2592000 -noout -in "$PKI/cert.pem" >/dev/null 2>&1; then
  echo "smtp-init: certificate and credentials still valid, nothing to do"
  exit 0
fi

echo "smtp-init: generating CA, server certificate (SAN smtp, localhost) and SMTP login"
rm -f "$PKI"/* "$SHARED"/*
W=$(mktemp -d)

# Private CA (EC P-256), 825 days. Its key stays in $W (tmpfs) and is discarded.
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 825 \
  -subj "/CN=Null City SMTP CA" -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign" -keyout "$W/ca.key" -out "$W/ca.pem" 2>/dev/null

# Server leaf: SAN `smtp` is the name the app connects to; localhost/127.0.0.1
# are for the HTTPS web UI opened from the host.
printf '%s\n' "subjectAltName=DNS:smtp,DNS:localhost,IP:127.0.0.1" \
  "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" \
  "extendedKeyUsage=serverAuth" > "$W/ext.cnf"
openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
  -subj "/CN=smtp" -keyout "$W/key.pem" -out "$W/csr.pem" 2>/dev/null
openssl x509 -req -in "$W/csr.pem" -CA "$W/ca.pem" -CAkey "$W/ca.key" -CAcreateserial \
  -days 825 -sha256 -extfile "$W/ext.cnf" -out "$W/cert.pem" 2>/dev/null

# SMTP login: taken from .env if provided, otherwise random (never committed).
USER_NAME=${SMTP_USER:-nullcity}
PASS=${SMTP_PASSWORD:-$(openssl rand -hex 24)}
printf '%s:%s\n' "$USER_NAME" "$PASS" > "$PKI/auth.txt"
printf '%s' "$USER_NAME" > "$SHARED/user"
printf '%s' "$PASS" > "$SHARED/password"

cp "$W/key.pem" "$PKI/key.pem"; cp "$W/cert.pem" "$PKI/cert.pem"
cp "$W/ca.pem" "$SHARED/ca.pem"
rm -rf "$W"

# Ownership/permissions: the key is readable by Mailpit only, the password by the app only.
# chmod BEFORE chown: after chown root no longer owns the file (and has no FOWNER).
chmod 0400 "$PKI/key.pem" "$PKI/auth.txt"; chmod 0444 "$PKI/cert.pem"
chmod 0444 "$SHARED/ca.pem"; chmod 0400 "$SHARED/user" "$SHARED/password"
chown "$MAILPIT_UID:$MAILPIT_UID" "$PKI/key.pem" "$PKI/auth.txt" "$PKI/cert.pem"
chown "$APP_UID:$APP_UID" "$SHARED/ca.pem" "$SHARED/user" "$SHARED/password"
echo "smtp-init: done"
