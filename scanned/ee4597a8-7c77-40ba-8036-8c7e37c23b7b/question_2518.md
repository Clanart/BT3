# Q2518: new accept invalid consensus data via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `new` in `crates/chia-puzzle-types/src/puzzles/did.rs` with royalty and settlement puzzle fields when equivalent-looking encodings are mixed make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/did.rs:21` / `new`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `new` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
