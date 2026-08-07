# Q2736: purge_files_in_range grows memory without an enforced bound (blockstore_purge.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `purge_files_in_range` in `ledger/src/blockstore/blockstore_purge.rs` with an interleaving where the write lands between the read and the validation, and grow the buffer `purge_files_in_range` feeds without any eviction bound taking effect, so that the invariant "Every container this path writes into has an enforced capacity or eviction policy." breaks and the result is DoS?

## Target
- File/function: `ledger/src/blockstore/blockstore_purge.rs` -> `purge_files_in_range()` (around line 322)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an interleaving where the write lands between the read and the validation
- Exploit idea: Repeatedly drive `purge_files_in_range` so a buffer, map, or cache it feeds grows without eviction, exhausting node memory below the cost the attacker pays.
- Invariant to test: Every container this path writes into has an enforced capacity or eviction policy.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Stress the path and assert the container's size plateaus rather than growing linearly with attacker input.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can perform syscall, CPI, memory-mapping, or deserialization work whose real CPU/memory cost far exceeds the compute units charged, degrading nodes below true cost.
