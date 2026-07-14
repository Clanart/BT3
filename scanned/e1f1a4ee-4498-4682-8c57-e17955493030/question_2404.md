# Q2404: decode number collapse distinct inputs into one accepted state via allocator node pairs and atoms

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `decode_number` in `crates/clvm-traits/src/int_encoding.rs` with allocator node pairs and atoms when equivalent-looking encodings are mixed make chia_rs collapse distinct inputs into one accepted state, violating the invariant that curried argument hashes match executed programs, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/clvm-traits/src/int_encoding.rs:35` / `decode_number`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: allocator node pairs and atoms
- Exploit idea: Drive `decode_number` through its public caller path using allocator node pairs and atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: curried argument hashes match executed programs
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
