# Q1626: new treat malformed data as a valid empty/default value via serialized block generator bytes

## Question
Can an unprivileged attacker call run_block_generator/run_block_generator2 through Rust or Python bindings targeting `new` in `crates/chia-consensus/src/build_interned_block.rs` with serialized block generator bytes when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:96` / `new`
- Entrypoint: call run_block_generator/run_block_generator2 through Rust or Python bindings
- Attacker controls: serialized block generator bytes
- Exploit idea: Drive `new` through its public caller path using serialized block generator bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: construct compressed and uncompressed equivalents and compare additions/removals.
