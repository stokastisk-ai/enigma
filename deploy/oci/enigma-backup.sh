#!/usr/bin/env bash
# Nightly encrypted PostgreSQL backup (systemd enigma-backup.timer). pg_dump -Fc inside the db container -> age-encrypted to the OPERATOR's public key
# (the private key is not on the VM, so a VM compromise cannot read old backups) -> /var/backups/enigma (7 kept) -> optional upload to the private
# OCI Object Storage bucket named in /opt/enigma/secrets/backup-bucket.txt, authenticated as the VM's INSTANCE PRINCIPAL (dynamic group + a policy
# limited to creating/inspecting objects in that one bucket; no API key, PAR or password on the VM). Bucket lifecycle deletes objects after 30 days.
# Upload failure never deletes the local copy. Bounded: 2 objects per day (<~5 MB each today), well inside 20 GB / 50k requests per month.
set -euo pipefail
umask 077
S=/opt/enigma/secrets; OUT=/var/backups/enigma; TS=$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0700 "$OUT"
RECIPIENT=$(cat "$S/backup-age-recipient.txt")                 # age1… public key only
cd /opt/enigma/src/deploy/oci
docker compose --env-file "$S/compose.env" exec -T db pg_dump -U postgres -d enigma -Fc --no-owner \
  | age -r "$RECIPIENT" > "$OUT/enigma-$TS.dump.age"
sha256sum "$OUT/enigma-$TS.dump.age" | awk '{print $1}' > "$OUT/enigma-$TS.dump.age.sha256"
ls -1t "$OUT"/enigma-*.dump.age 2>/dev/null | tail -n +8 | while read -r f; do rm -f -- "$f" "$f.sha256"; done
if [ -s "$S/backup-bucket.txt" ]; then
  BUCKET=$(cat "$S/backup-bucket.txt")
  for f in "enigma-$TS.dump.age" "enigma-$TS.dump.age.sha256"; do
    docker run --rm -e OCI_CLI_AUTH=instance_principal -v "$OUT:/b:ro" \
      ghcr.io/oracle/oci-cli@sha256:8e1b4831170a37b89921da4094d242c7b74b8bd82e7c46ea340d7a09eab310a0 \
      os object put --bucket-name "$BUCKET" --file "/b/$f" --name "$f" --no-overwrite >/dev/null 2>&1 \
      || { echo "upload failed for $f (local copy kept)"; exit 3; }
  done
fi
echo "backup ok: enigma-$TS.dump.age ($(stat -c %s "$OUT/enigma-$TS.dump.age") bytes)"
