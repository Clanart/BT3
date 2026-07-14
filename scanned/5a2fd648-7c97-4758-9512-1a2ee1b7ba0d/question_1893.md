# Q1893: from bytes commit output after an error path via infinity and subgroup edge cases

## Question
Can an unprivileged attacker derive keys from attacker-controlled indexes targeting `from_bytes` in `crates/chia-secp/src/secp256r1/public_key.rs` with infinity and subgroup edge cases when duplicate or prefix-colliding items are present make chia_rs commit output after an error path, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-secp/src/secp256r1/public_key.rs:45` / `from_bytes`
- Entrypoint: derive keys from attacker-controlled indexes
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `from_bytes` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: test cache update/evict paths with message-public-key collisions.
