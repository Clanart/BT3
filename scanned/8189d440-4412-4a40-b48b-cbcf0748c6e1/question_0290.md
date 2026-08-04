# Q290: getTokenAccountsByOwner filter optimizer bypass

## Question
Can an unprivileged attacker enter through `getTokenAccountsByOwner` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_owner` hits a path where attacker-chosen filter order or encoding causes the request to bypass the cheapest pruning path, breaking the invariant that filter order and encoding should not bypass the cheapest index-assisted path and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_owner
- Entrypoint: JSON-RPC `getTokenAccountsByOwner` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: look for legal filter combinations that disable early pruning
- Invariant to test: filter order and encoding should not bypass the cheapest index-assisted path
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: permute filters while keeping semantics constant and trace candidate-account counts
