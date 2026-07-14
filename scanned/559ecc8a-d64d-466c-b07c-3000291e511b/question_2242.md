# Q2242: RequestRemoveCoinSubscriptions accept invalid consensus data via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `RequestRemoveCoinSubscriptions` in `crates/chia-protocol/src/wallet_protocol.rs` with trusted vs untrusted parse mode inputs when serialized bytes are validly framed but semantically adversarial make chia_rs accept invalid consensus data, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/wallet_protocol.rs:229` / `RequestRemoveCoinSubscriptions`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `RequestRemoveCoinSubscriptions` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
