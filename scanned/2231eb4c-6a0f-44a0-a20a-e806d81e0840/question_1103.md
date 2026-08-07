# Q1103: roots accepts input it should reject (status_cache.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `roots` in `runtime/src/status_cache.rs` with an empty or single-element set at the boundary of the accumulation, and have `roots` accept input that fails the property it is supposed to prove, so that the invariant "`roots` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/status_cache.rs` -> `roots()` (around line 196)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an empty or single-element set at the boundary of the accumulation
- Exploit idea: Construct input that `roots` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `roots` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `roots` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy or upgrade a program, or poison the program cache, so honest validators execute different bytecode or produce different results for the same instruction.
