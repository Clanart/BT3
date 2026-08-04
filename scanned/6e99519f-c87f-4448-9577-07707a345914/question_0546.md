# Q546: getFirstAvailableBlock single-request crash path

## Question
Can an unprivileged attacker enter through `getFirstAvailableBlock` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_first_available_block` hits a path where a validly encoded historical query may still reach an assertion or fatal allocation path, breaking the invariant that historical rpc queries must not crash the node under single-client input and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_first_available_block
- Entrypoint: JSON-RPC `getFirstAvailableBlock` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: treat the method as a crash surface, not just a slow path surface
- Invariant to test: historical RPC queries must not crash the node under single-client input
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: fuzz valid range/cursor/detail combinations and stop on crashes
