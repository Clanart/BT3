# Q105: new treat malformed data as a valid empty/default value via compressed spend bundle backrefs

## Question
Can an unprivileged attacker submit a block generator targeting `new` in `crates/chia-consensus/src/build_interned_block.rs` with compressed spend bundle backrefs when equivalent-looking encodings are mixed make chia_rs treat malformed data as a valid empty/default value, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:96` / `new`
- Entrypoint: submit a block generator
- Attacker controls: compressed spend bundle backrefs
- Exploit idea: Drive `new` through its public caller path using compressed spend bundle backrefs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: run both generator paths and compare costs, spends, and errors.
