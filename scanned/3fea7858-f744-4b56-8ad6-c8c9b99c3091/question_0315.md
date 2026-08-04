# Q315: getTokenAccountsByDelegate selective-history coupling

## Question
Can an unprivileged attacker enter through `getTokenAccountsByDelegate` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_delegate` hits a path where the filtered lookup may pull unexpectedly broad history or cache state rather than only current indexed state, breaking the invariant that indexed current-state rpc must not couple to broad history traversal and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: check whether current-state indexed requests accidentally force broader history work
- Invariant to test: indexed current-state RPC must not couple to broad history traversal
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: profile cold-start indexed queries
