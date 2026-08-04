# Q458: getBlockTime not-found slow path

## Question
Can an unprivileged attacker enter through `getBlockTime` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_block_time` hits a path where adversarial not-found inputs cost materially more than hits and can be repeated by one client, breaking the invariant that rejecting missing history objects should not be the expensive case and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_block_time
- Entrypoint: JSON-RPC `getBlockTime` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: stress misses rather than hits
- Invariant to test: rejecting missing history objects should not be the expensive case
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: benchmark hit/miss asymmetry for slots and signatures
