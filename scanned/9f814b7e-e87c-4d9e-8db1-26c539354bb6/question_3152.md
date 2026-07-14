# Q3152: py new derive a different canonical hash via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker call run_block_generator/run_block_generator2 through Rust or Python bindings targeting `py_new` in `crates/chia-consensus/src/build_interned_block.rs` with singleton fast-forward lineage proof fields when the payload is accepted by one public API before another validates it make chia_rs derive a different canonical hash, violating the invariant that CLVM cost accounting is monotonic and bounded, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:236` / `py_new`
- Entrypoint: call run_block_generator/run_block_generator2 through Rust or Python bindings
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `py_new` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM cost accounting is monotonic and bounded
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: run both generator paths and compare costs, spends, and errors.
