# Q1031: SingletonSolution allow replay across contexts via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `SingletonSolution` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with royalty and settlement puzzle fields when duplicate or prefix-colliding items are present make chia_rs allow replay across contexts, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:58` / `SingletonSolution`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `SingletonSolution` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
