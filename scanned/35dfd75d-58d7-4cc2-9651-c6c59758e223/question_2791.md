# Q2791: verify mis-order operations across a batch via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker call the public Python API targeting `verify` in `wheel/src/api.rs` with Python lists of tuple spend inputs when serialized bytes are validly framed but semantically adversarial make chia_rs mis-order operations across a batch, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:345` / `verify`
- Entrypoint: call the public Python API
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `verify` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
