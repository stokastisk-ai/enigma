# Deployment (OCI Always Free)

Status: implemented on branch `migration/oci-always-free`, verified locally (x86 compose smoke, real-data import dry run); **not yet deployed**
(Ampere A1 capacity pending). The previous Railway + Neon deployment stays live until the owner approves the cutover.

## Resources (all Always Free, home region)
One `VM.Standard.A1.Flex` (2 OCPU / 12 GB, Ubuntu 24.04 aarch64, 50 GB boot) in a dedicated compartment guarded by a quota policy that allows only
that shape and size and at most 100 GB of block storage. One VCN/public subnet; security list: 443 and 80 public, 22 from the operator address only.
No load balancer, managed database, Functions or API Gateway. Optional private Object Storage bucket for encrypted backups (write-only PAR upload).

## Stack (`deploy/oci/compose.yaml`)
| Service | Exposure | Notes |
|---|---|---|
| caddy | 80/443 | automatic Let's Encrypt for `ENIGMA_HOST`, which can be the VM's public IPv4 address (free IP certificate, `shortlived` 160 h profile, renewed automatically; no domain needed); port 80 = ACME + redirect; HSTS; only web + the API runtime plane routed |
| web | private | Next.js standalone, self-hosted Better Auth (schema `auth`), BFF to `http://api:8000` |
| api | private (runtime plane via caddy) | FastAPI, `ENIGMA_AUTH_PROVIDER=betterauth`, JWKS from `http://web:3000/api/auth/jwks` |
| db | internal network, no egress | PostgreSQL 18.7, TLS 1.3 from a local CA (`db-tls.sh`); clients use `sslmode=verify-full` |
| migrate / ops | on demand (profile `ops`) | schema + least-privilege roles; import and key tooling |

Images are built natively on the ARM VM; PostgreSQL and Caddy images are pinned by digest. Containers run non-root where the image allows,
read-only root filesystems for api/web, memory limits, `no-new-privileges`, bounded `local` log driver, restart `unless-stopped`.

## First deployment
1. `deploy/oci/db-tls.sh`; create `/opt/enigma/secrets/{compose,db,migrate,api,web}.env` (mode 0600) from the `*.env.example` templates.
2. `docker compose --env-file /opt/enigma/secrets/compose.env build`, then `up -d db`.
3. Import existing data (optional): `--profile ops run --rm ops python3 /work/oci_import.py ... --dry-run`, then without `--dry-run`.
4. `--profile ops run --rm migrate` (idempotent: migrations 0001-0006, roles `enigma_api` and `enigma_web_auth`).
5. `up -d`; install `deploy/oci/systemd/*` and enable `enigma.service`, `enigma-backup.timer`, `enigma-prune.timer`, `enigma-certcheck.timer`.

## Operations
Status `docker compose ps`; logs `docker compose logs --tail 200 <service>`; restart `docker compose restart <service>`; nightly encrypted backup
(`enigma-backup.sh`, age-encrypted to an operator key that is not on the VM, uploaded as the VM's instance principal); daily retention
(`enigma-prune.sh`; an optional `ENIGMA_RETENTION_HOLD_UNTIL` of at most 7 days keeps finished-job events during a migration); TLS monitor every
6 h (`enigma-certcheck.sh`: chain + name + at least 72 h left, alert through OCI Notifications). Sign-up is closed
(`ENIGMA_PUBLIC_SIGNUP=off`, private beta) until a trusted mail sender on an owner-controlled domain is configured.

## GPU host
Kaggle notebooks 01/02/03 in `docs/kaggle/diagnostics/` connect outbound over HTTPS; nothing is exposed from Kaggle. Kaggle Secrets:
`ENIGMA_DIAG_KEY`, `ENIGMA_RUNTIME_TOKEN`.
