# Local validation

2026-10-12: 34 tests pass on Python 3.12.14 / Linux. Tests use API mocks,
local Git fixtures and real network-free process trees. Node 24.19.0 and
locked Wrangler 4.149.0 were checked locally during preparation.

Process regressions include timeout, SIGTERM cancellation, successful/failed
wrapper exit with descendants, TERM-ignoring children, a detached grandchild,
reaping, stopped heartbeat writes, and teardown before API checks/lock release.
Alias tests reject missing/wrong aliases and old/wrong served content.
Production configuration must be an explicitly returned object; absence,
null, wrong types and post-upload changes fail closed.

No real token, Cloudflare request, upload, Actions execution, billing change
or protection configuration was used. Live API/schema/CDN behavior and actual
GitHub protection enforcement remain unverified. Activation is blocked until
required protection rules are applied and their enforcement confirmed.

This repository is a fresh code-only snapshot. No previous repository history,
private operational records, correspondence, credentials or site assets are included.
