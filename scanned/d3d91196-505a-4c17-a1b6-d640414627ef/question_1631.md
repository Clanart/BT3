# Q1631: py new derive a different canonical hash via trusted-block coin spend extraction inputs

## Question
Can an unprivileged attacker submit a block generator targeting `py_new` in `crates/chia-consensus/src/build_interned_block.rs` with trusted-block coin spend extraction inputs when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that CLVM cost accounting is monotonic and bounded, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:236` / `py_new`
- Entrypoint: submit a block generator
- Attacker controls: trusted-block coin spend extraction inputs
- Exploit idea: Drive `py_new` through its public caller path using trusted-block coin spend extraction inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM cost accounting is monotonic and bounded
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
