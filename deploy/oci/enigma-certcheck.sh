#!/usr/bin/env bash
# TLS monitor (enigma-certcheck.timer, every 6 h). Connects to the PUBLIC endpoint exactly as a client does, verifies the chain against the system
# trust store AND the host/IP name, and requires >= 72 h of validity (Let's Encrypt short-lived certificates last 160 h; Caddy renews around 1/3 left).
# On failure: exit non-zero (visible in `systemctl --failed` and the journal) and, if /opt/enigma/secrets/alert-topic.ocid exists, publish one OCI
# Notifications message using the VM's INSTANCE PRINCIPAL (no API key on the VM). Never prints certificate private material.
set -uo pipefail
S=${ENIGMA_SECRETS:-/opt/enigma/secrets}
HOST=$(grep -oP '^ENIGMA_HOST=\K.*' "$S/compose.env")
MIN_H=${MIN_H:-72}
if [[ "$HOST" =~ ^[0-9.]+$ ]]; then NAMECHK=(-verify_ip "$HOST"); SNI=(); else NAMECHK=(-verify_hostname "$HOST"); SNI=(-servername "$HOST"); fi
PEM=$(echo | timeout 20 openssl s_client -connect "$HOST:443" "${SNI[@]}" -verify_return_error -CApath /etc/ssl/certs "${NAMECHK[@]}" 2>/dev/null \
      | openssl x509 2>/dev/null)
problem=""
if [ -z "$PEM" ]; then
  problem="TLS handshake or chain/name verification failed for $HOST"
else
  END=$(date -d "$(echo "$PEM" | openssl x509 -noout -enddate | cut -d= -f2)" +%s); LEFT_H=$(( (END - $(date +%s)) / 3600 ))
  [ "$LEFT_H" -lt "$MIN_H" ] && problem="certificate for $HOST expires in ${LEFT_H} h (< ${MIN_H} h): renewal is not happening"
fi
if [ -z "$problem" ]; then echo "certcheck ok: $HOST verified, ${LEFT_H} h left"; exit 0; fi
echo "certcheck FAILED: $problem"
if [ -s "$S/alert-topic.ocid" ]; then
  docker run --rm -e OCI_CLI_AUTH=instance_principal ghcr.io/oracle/oci-cli@sha256:8e1b4831170a37b89921da4094d242c7b74b8bd82e7c46ea340d7a09eab310a0 \
    ons message publish --topic-id "$(cat "$S/alert-topic.ocid")" --title "Enigma TLS alert" --body "$problem" >/dev/null 2>&1 \
    || echo "alert publish failed (instance principal / policy / topic): check IAM"
fi
exit 2
