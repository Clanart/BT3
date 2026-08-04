# Q305: getTokenAccountsByOwner slow-path lock contention

## Question
Can an unprivileged attacker use `getTokenAccountsByOwner` within the single-client low-rate model and choose filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled such that `get_token_accounts_by_owner` triggers a path where this method may hold a shared lock or guard long enough to degrade other request classes, violating the invariant that one heavy request must not monopolize shared locks at low rate and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_owner
- Entrypoint: JSON-RPC `getTokenAccountsByOwner` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: treat lock hold time as the exploit surface
- Invariant to test: one heavy request must not monopolize shared locks at low rate
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: trace lock hold times during boundary requests
