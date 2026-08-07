# Q2120: building_off_previous_leader_last_block can be driven into unbounded work (poh_recorder.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `building_off_previous_leader_last_block` in `poh/src/poh_recorder.rs` with two transactions in one batch that conflict on an account only one of them declares, and make `building_off_previous_leader_last_block` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `building_off_previous_leader_last_block` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `poh/src/poh_recorder.rs` -> `building_off_previous_leader_last_block()` (around line 905)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: two transactions in one batch that conflict on an account only one of them declares
- Exploit idea: Grow the attacker-controlled collection `building_off_previous_leader_last_block` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `building_off_previous_leader_last_block` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `building_off_previous_leader_last_block` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can craft bytecode, ELF metadata, precompile data, or zk proof input that panics, aborts, infinite-loops, or exhausts memory inside the VM and halts every validator.
