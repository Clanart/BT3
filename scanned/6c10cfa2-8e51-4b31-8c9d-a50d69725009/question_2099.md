# Q2099: new_random_states can be driven into unbounded work (deduper.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `new_random_states` in `perf/src/deduper.rs` with arguments that drive the path into its error branch after side effects were applied, and make `new_random_states` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `new_random_states` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `perf/src/deduper.rs` -> `new_random_states()` (around line 117)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Grow the attacker-controlled collection `new_random_states` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `new_random_states` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `new_random_states` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can craft bytecode, ELF metadata, precompile data, or zk proof input that panics, aborts, infinite-loops, or exhausts memory inside the VM and halts every validator.
