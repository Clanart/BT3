# Q1625: new with overflow or underflow a boundary check via trusted-block coin spend extraction inputs

## Question
Can an unprivileged attacker call run_block_generator/run_block_generator2 through Rust or Python bindings targeting `new_with` in `crates/chia-consensus/src/build_interned_block.rs` with trusted-block coin spend extraction inputs when the payload is accepted by one public API before another validates it make chia_rs overflow or underflow a boundary check, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:81` / `new_with`
- Entrypoint: call run_block_generator/run_block_generator2 through Rust or Python bindings
- Attacker controls: trusted-block coin spend extraction inputs
- Exploit idea: Drive `new_with` through its public caller path using trusted-block coin spend extraction inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz generator refs/backrefs and assert deterministic output.
