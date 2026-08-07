# Q2871: get_leader_info can serve state that disagrees with the cache (transaction_client.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `get_leader_info` in `send-transaction-service/src/transaction_client.rs` with a missing entry that makes the loader fall back to a default instead of failing, and make the token amount and decimals reported disagree with the mint's real decimals and raw amount, so that the invariant "Cached and freshly-loaded values are observationally identical at every commit point." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `send-transaction-service/src/transaction_client.rs` -> `get_leader_info()` (around line 90)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a missing entry that makes the loader fall back to a default instead of failing
- Exploit idea: Make `get_leader_info` read a cached value the attacker already invalidated, so a node with a warm cache commits different state than one that reloaded.
- Invariant to test: Cached and freshly-loaded values are observationally identical at every commit point.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Test the path with the cache primed and cleared; assert the committed state is identical in both runs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy or upgrade a program, or poison the program cache, so honest validators execute different bytecode or produce different results for the same instruction.
