# Q311: getTokenAccountsByDelegate miss-path blowup

## Question
Can an unprivileged attacker enter through `getTokenAccountsByDelegate` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_delegate` hits a path where selective filters that match nothing may still be more expensive than filters that match something, breaking the invariant that no-match filtered queries should fail or finish cheaply and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: exercise expensive-empty queries rather than only positive queries
- Invariant to test: no-match filtered queries should fail or finish cheaply
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: benchmark highly selective misses against small positive matches
