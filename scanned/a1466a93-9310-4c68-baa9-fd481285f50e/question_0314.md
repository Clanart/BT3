# Q314: getTokenAccountsByDelegate single-request panic surface

## Question
Can an unprivileged attacker enter through `getTokenAccountsByDelegate` and supply filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled so that `get_token_accounts_by_delegate` hits a path where filter parsing, optimization, or encoded output generation may assume impossible states reachable from legal input, breaking the invariant that filtered indexed rpc should not panic on validly encoded inputs and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_token_accounts_by_delegate
- Entrypoint: JSON-RPC `getTokenAccountsByDelegate` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: filtered query params, account/pubkey inputs, encoding flags, and a node where the relevant secondary index is enabled
- Exploit idea: use only in-scope filtered, indexed requests and look for crashable assumptions
- Invariant to test: filtered indexed RPC should not panic on validly encoded inputs
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: fuzz filter shapes, offsets, memcmp payloads, and encodings
