# Q3311: flip bits produce a Rust/Python disagreement via public key and signature byte encodings

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `flip_bits` in `crates/chia-bls/src/secret_key.rs` with public key and signature byte encodings when the same payload is parsed through public bindings make chia_rs produce a Rust/Python disagreement, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/secret_key.rs:35` / `flip_bits`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `flip_bits` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
