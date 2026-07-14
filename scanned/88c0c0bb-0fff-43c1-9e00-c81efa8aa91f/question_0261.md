# Q261: str treat malformed data as a valid empty/default value via duplicate public-key/message pairs

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `__str__` in `crates/chia-bls/src/public_key.rs` with duplicate public-key/message pairs when duplicate or prefix-colliding items are present make chia_rs treat malformed data as a valid empty/default value, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:371` / `__str__`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `__str__` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
