#!/usr/bin/env bash
# Daily retention (replaces the old GitHub Actions cron): calls the API's admin prune route over the private network with CRON_SECRET.
set -euo pipefail
cd /opt/enigma/src/deploy/oci
docker compose --env-file /opt/enigma/secrets/compose.env exec -T api python -c '
import json, os, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/admin/prune", headers={"authorization": "Bearer " + os.environ["CRON_SECRET"]})
print(json.dumps(json.load(urllib.request.urlopen(req, timeout=60)))[:500])'
