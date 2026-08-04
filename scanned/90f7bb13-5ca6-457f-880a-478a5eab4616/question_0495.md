# Q495: getBlocksWithLimit cold-cache amplification

## Question
Can an unprivileged attacker use `getBlocksWithLimit` within the single-client low-rate model and choose slot/signature/range params, commitment, encoding flags, and pagination cursors such that `get_blocks_with_limit` triggers a path where a legal cold-start request shape costs materially more than the warm path and is attacker-repeatable, violating the invariant that cold-path cost should still be bounded for a single client and causing `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_blocks_with_limit
- Entrypoint: JSON-RPC `getBlocksWithLimit` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: force cold reads, cache misses, or empty caches through one public method
- Invariant to test: cold-path cost should still be bounded for a single client
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: alternate request parameters to defeat warm caches
