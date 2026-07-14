# Q1023: new skip a required validation guard via synthetic key derivation inputs

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `new` in `crates/chia-puzzle-types/src/puzzles/offer.rs` with synthetic key derivation inputs when the same payload is parsed through public bindings make chia_rs skip a required validation guard, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/offer.rs:30` / `new`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `new` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
