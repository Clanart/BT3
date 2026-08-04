# Q309: getTokenAccountsByDelegate response amplification

## Question
Can an unprivileged attacker enter through `getTokenAccountsByDelegate` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_delegate` hits a path where a narrow request still materializes oversized keyed-account responses or duplicates data during formatting, breaking the invariant that returned keyed-account objects must stay proportional to the filtered result set and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: measure whether response-building cost dominates the actual lookup
- Invariant to test: returned keyed-account objects must stay proportional to the filtered result set
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: select a tiny filtered result set and compare serialization cost across encodings
