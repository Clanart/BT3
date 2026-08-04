# Q317: getTokenAccountsByDelegate shared-executor fairness

## Question
Can an unprivileged attacker use `getTokenAccountsByDelegate` within the single-client low-rate model and choose filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled such that `get_token_accounts_by_delegate` triggers a path where a single client can make this method occupy shared async worker capacity long enough to measurably degrade unrelated cheap methods, violating the invariant that rpc worker time should remain fairly shareable under one-client use and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: focus on cross-method impact, not just local latency
- Invariant to test: RPC worker time should remain fairly shareable under one-client use
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: run a cheap control RPC in parallel and quantify latency inflation
