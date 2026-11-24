# Decisions

1. **Canonical shadcn/ui only; no shadcn.io components.** shadcn.io is a separate, subscription-licensed Pro product whose terms forbid republishing its components; the charter forbids unlicensed components. Chat bubble, scroller and prompt input are original code on MIT shadcn primitives (`@base-ui/react`, style `base-nova`). The `cn` npm package (MIT, `github.com/shadcn-ui/cn`) is the CLI's own class-merge helper.
2. **One chart engine**: Recharts through shadcn `ChartContainer`. Series colours = dataviz categorical slots 1-3, validated light+dark for all-pairs CVD separation; aqua-on-light below 3:1 is covered by legends, tooltips and per-chart data tables.
3. **FastAPI owns domain logic behind a `Store` interface** (Memory for tests/mock, Supabase in prod) and atomic logic lives in SQL functions, so correctness holds even if a route has a bug. FastAPI uses the service role after explicit authz; browsers get SELECT-only RLS and Realtime receive.
4. **Own workspace tables, not Clerk Organizations** (billing-independent onboarding).
5. **Events table is the source of truth; Realtime is an accelerator.** Polling with `after_seq` is always correct.
6. **Chunks, not tokens** are reported from streaming (honesty over a guess).
7. **Retry resubmits the prompt** (adds a duplicate user turn). A server-side retry that reuses the turn is future work.
8. **`cacheComponents` off, all routes dynamic**: auth config must be read at runtime.
9. **`shadcn` CLI is a devDependency** (its minimatch advisory chain is build-time tooling); production deps audit clean.
10. **Grafana (AGPL-3.0-only) is inspiration only**: left nav, global time range, per-panel error/empty states, table-view drill-down. No code copied.
11. Dev auth exists solely for local E2E and is blocked in production and on Railway.
