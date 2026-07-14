# Q614: from derive a different canonical hash via sized integer boundary values

## Question
Can an unprivileged attacker compare trusted and untrusted parse modes targeting `from` in `crates/chia-protocol/src/bytes.rs` with sized integer boundary values when the attacker can choose ordering inside a batch make chia_rs derive a different canonical hash, violating the invariant that streamable parsing rejects non-canonical or trailing consensus bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:422` / `from`
- Entrypoint: compare trusted and untrusted parse modes
- Attacker controls: sized integer boundary values
- Exploit idea: Drive `from` through its public caller path using sized integer boundary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: streamable parsing rejects non-canonical or trailing consensus bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: differential-test JSON dict conversion against streamable bytes.
