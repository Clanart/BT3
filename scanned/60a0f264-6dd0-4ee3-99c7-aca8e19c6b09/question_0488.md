# Q488: from overflow or underflow a boundary check via FullBlock/HeaderBlock byte streams

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `from` in `crates/chia-protocol/src/program.rs` with FullBlock/HeaderBlock byte streams when serialized bytes are validly framed but semantically adversarial make chia_rs overflow or underflow a boundary check, violating the invariant that trusted and untrusted parsing cannot disagree on valid network bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/program.rs:102` / `from`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: FullBlock/HeaderBlock byte streams
- Exploit idea: Drive `from` through its public caller path using FullBlock/HeaderBlock byte streams; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted and untrusted parsing cannot disagree on valid network bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: mutate each serialized field and assert hash or validation changes.
