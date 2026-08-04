# Q294: getTokenAccountsByOwner encoding-driven heap growth

## Question
Can an unprivileged attacker enter through `getTokenAccountsByOwner` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_owner` hits a path where heavier encodings cause avoidable cloning or heap retention for attacker-selected account layouts, breaking the invariant that encoding choice should not let one filtered request dominate heap usage and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_owner
- Entrypoint: JSON-RPC `getTokenAccountsByOwner` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: treat encoding as the attack lever even when the filter is selective
- Invariant to test: encoding choice should not let one filtered request dominate heap usage
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: replay identical filtered queries across encodings
