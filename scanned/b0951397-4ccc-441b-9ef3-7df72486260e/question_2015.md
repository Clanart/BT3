# Q2015: is_one charges far less than it costs (lib.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `is_one` in `unified-scheduler-logic/src/lib.rs` with arguments that drive the path into its error branch after side effects were applied, and make the real CPU/memory cost of `is_one` exceed the units charged for it, so that the invariant "Charged cost is a monotone upper bound on real cost for every input shape." breaks and the result is DoS?

## Target
- File/function: `unified-scheduler-logic/src/lib.rs` -> `is_one()` (around line 171)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Find the input shape where `is_one`'s real CPU/memory/IO cost grows much faster than the compute units or fee charged for it.
- Invariant to test: Charged cost is a monotone upper bound on real cost for every input shape.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Measure real time/allocations versus units charged across input sizes; assert cost/CU ratio stays bounded.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. An unprivileged attacker can perform syscall, CPI, memory-mapping, or deserialization work whose real CPU/memory cost far exceeds the compute units charged, degrading nodes below true cost.
