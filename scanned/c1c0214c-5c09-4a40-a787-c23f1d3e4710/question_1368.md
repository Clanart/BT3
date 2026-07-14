# Q1368: update digest commit output after an error path via newtype and enum field encodings

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `update_digest` in `crates/chia-traits/src/streamable.rs` with newtype and enum field encodings when serialized bytes are validly framed but semantically adversarial make chia_rs commit output after an error path, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/streamable.rs:171` / `update_digest`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `update_digest` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: compare generated parse/stream/hash with hand-encoded bytes.
