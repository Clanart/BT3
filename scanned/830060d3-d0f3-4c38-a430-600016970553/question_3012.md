# Q3012: get_leader_tpus can serve state that disagrees with the cache (tpu_info.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `get_leader_tpus` in `send-transaction-service/src/tpu_info.rs` with a key that exists on an ancestor fork but not the current one, and make the block the status lookup reads disagree with the block that is actually rooted, so that the invariant "Cached and freshly-loaded values are observationally identical at every commit point." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `send-transaction-service/src/tpu_info.rs` -> `get_leader_tpus()` (around line 15)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a key that exists on an ancestor fork but not the current one
- Exploit idea: Make `get_leader_tpus` read a cached value the attacker already invalidated, so a node with a warm cache commits different state than one that reloaded.
- Invariant to test: Cached and freshly-loaded values are observationally identical at every commit point.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Test the path with the cache primed and cleared; assert the committed state is identical in both runs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy or upgrade a program, or poison the program cache, so honest validators execute different bytecode or produce different results for the same instruction.
