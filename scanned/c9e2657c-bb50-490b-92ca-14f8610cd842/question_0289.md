# Q289: getTokenAccountsByOwner filtered-scan regression

## Question
Can an unprivileged attacker enter through `getTokenAccountsByOwner` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_owner` hits a path where a formally filtered request still falls back to a much broader scan than the filter suggests, breaking the invariant that a filtered request on an enabled secondary index must not devolve into unbounded account scans and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_owner
- Entrypoint: JSON-RPC `getTokenAccountsByOwner` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: verify that the in-scope filtered/index-enabled path does not silently degenerate into near-full scans
- Invariant to test: a filtered request on an enabled secondary index must not devolve into unbounded account scans
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run with the relevant secondary index enabled and compare visited accounts
