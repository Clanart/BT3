# Q1784: iadd allow replay across contexts via duplicate public-key/message pairs

## Question
Can an unprivileged attacker submit aggregate signature material targeting `__iadd__` in `crates/chia-bls/src/public_key.rs` with duplicate public-key/message pairs when duplicate or prefix-colliding items are present make chia_rs allow replay across contexts, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:380` / `__iadd__`
- Entrypoint: submit aggregate signature material
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `__iadd__` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Signature, aggregate signature, synthetic key, puzzle type, condition, timelock, or coin-id validation bypass enables unauthorized spend acceptance or replay
- Fast validation: compare aggregate_verify with independent pairings.
