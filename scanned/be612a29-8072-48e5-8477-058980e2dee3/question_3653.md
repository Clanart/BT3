# Q3653: from allow replay across contexts via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `from` in `crates/chia-protocol/src/bytes.rs` with streamable byte prefixes and trailing bytes when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:397` / `from`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `from` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz length prefixes and integer encodings and assert canonical rejection.
