# Q683: update digest allow replay across contexts via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `update_digest` in `crates/chia-protocol/src/utils.rs` with trusted vs untrusted parse mode inputs when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/utils.rs:5` / `update_digest`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `update_digest` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz length prefixes and integer encodings and assert canonical rejection.
