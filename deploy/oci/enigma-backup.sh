#!/usr/bin/env bash
# Nightly encrypted PostgreSQL backup (systemd enigma-backup.timer). pg_dump -Fc inside the db container -> age-encrypted to the OPERATOR's public key
# (the private key is not on the VM, so a VM compromise cannot read old backups) -> /var/backups/enigma (7 kept) -> optional upload to the private
# OCI Object Storage bucket through a write-only pre-authenticated request URL stored in /opt/enigma/secrets/backup-par.url (0600).
# Upload failure never deletes the local copy. Bounded: one object per day, Always Free budget 20 GB / 50k requests per month.
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
if [ -s "$S/backup-par.url" ]; then
  PAR=$(cat "$S/backup-par.url")
  for f in "enigma-$TS.dump.age" "enigma-$TS.dump.age.sha256"; do
    curl --fail --silent --show-error --max-time 300 --retry 3 --retry-delay 10 -T "$OUT/$f" "${PAR%/}/$f" >/dev/null \
      || { echo "upload failed for $f (local copy kept)"; exit 3; }
  done
fi
echo "backup ok: enigma-$TS.dump.age ($(stat -c %s "$OUT/enigma-$TS.dump.age") bytes)"
