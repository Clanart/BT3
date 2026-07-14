# Q1905: arbitrary commit output after an error path via infinity and subgroup edge cases

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `arbitrary` in `crates/chia-secp/src/secp256r1/signature.rs` with infinity and subgroup edge cases when serialized bytes are validly framed but semantically adversarial make chia_rs commit output after an error path, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-secp/src/secp256r1/signature.rs:31` / `arbitrary`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `arbitrary` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: compare aggregate_verify with independent pairings.
