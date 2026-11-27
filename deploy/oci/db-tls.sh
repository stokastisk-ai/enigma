#!/usr/bin/env bash
# One-time: local CA + server certificate for PostgreSQL (host name "db" on the private compose network). The API refuses production DSNs that are not
# sslmode=verify-full, so in-VM database traffic is TLS 1.3 with hostname verification. Keys never leave /opt/enigma/secrets/pg-tls (root/0700).
set -euo pipefail
D=${ENIGMA_SECRETS:-/opt/enigma/secrets}/pg-tls
install -d -m 0700 "$D"
cd "$D"
[ -f ca.key ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 -subj "/CN=enigma-local-pg-ca" -keyout ca.key -out ca.crt
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=db" -keyout server.key -out server.csr
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -out server.crt \
  -extfile <(printf "subjectAltName=DNS:db\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature")
rm -f server.csr
chown 999:999 server.key server.crt && chmod 0600 server.key && chmod 0644 server.crt ca.crt && chmod 0600 ca.key
chmod 0711 "$D"                                     # containers read ca.crt / server.* through bind mounts; ca.key stays root-only
echo "pg-tls ready: CA $(openssl x509 -in ca.crt -noout -fingerprint -sha256 | cut -d= -f2 | cut -c1-17)…"
