# Q483: getBlocksWithLimit historical walk amplification

## Question
Can an unprivileged attacker enter through `getBlocksWithLimit` and supply slot/signature/range params, commitment, encoding flags, and pagination cursors so that `get_blocks_with_limit` hits a path where attacker-selected slots, ranges, or signatures force traversal of a disproportionate amount of historical ledger state, breaking the invariant that a low-rate single client should not force near-unbounded historical traversal through one call and leading to `RPC DoS/Crash`?

## Target
- File/function: rpc/src/rpc.rs::get_blocks_with_limit
- Entrypoint: JSON-RPC `getBlocksWithLimit` from a single unauthenticated client at no more than once per `CLUSTER_SLOT_TIME_TARGET / 2`
- Attacker controls: slot/signature/range params, commitment, encoding flags, and pagination cursors
- Exploit idea: drive cold-history scans that are much larger than the nominal request
- Invariant to test: a low-rate single client should not force near-unbounded historical traversal through one call
- Expected Immunefi impact: RPC DoS/Crash
- Fast validation: trace key visits and RocksDB reads for boundary ranges
