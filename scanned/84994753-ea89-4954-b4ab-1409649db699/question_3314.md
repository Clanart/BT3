# Q3314: sha256 overflow or underflow a boundary check via infinity and subgroup edge cases

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `sha256` in `crates/chia-bls/src/secret_key.rs` with infinity and subgroup edge cases when duplicate or prefix-colliding items are present make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/secret_key.rs:75` / `sha256`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: infinity and subgroup edge cases
- Exploit idea: Drive `sha256` through its public caller path using infinity and subgroup edge cases; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
