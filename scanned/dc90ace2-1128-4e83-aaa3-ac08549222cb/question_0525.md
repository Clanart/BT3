# Q525: getSignaturesForAddress response retention

## Question
Can an unprivileged attacker enter through `getSignaturesForAddress` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_signatures_for_address` hits a path where large historical objects survive in memory across one request longer than necessary, breaking the invariant that the method should release large history objects promptly after serializing them and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_signatures_for_address
- Entrypoint: JSON-RPC `getSignaturesForAddress` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: look for retained vectors or intermediate objects that outlive response emission
- Invariant to test: the method should release large history objects promptly after serializing them
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: capture heap profiles during large-history requests
