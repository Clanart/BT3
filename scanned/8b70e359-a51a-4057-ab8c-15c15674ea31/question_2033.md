# Q2033: program roundtrip overflow or underflow a boundary check via unfinished block payloads

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `program_roundtrip` in `crates/chia-protocol/src/program.rs` with unfinished block payloads when the attacker can choose ordering inside a batch make chia_rs overflow or underflow a boundary check, violating the invariant that trusted and untrusted parsing cannot disagree on valid network bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/program.rs:513` / `program_roundtrip`
- Entrypoint: submit serialized block or spend data
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `program_roundtrip` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted and untrusted parsing cannot disagree on valid network bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
