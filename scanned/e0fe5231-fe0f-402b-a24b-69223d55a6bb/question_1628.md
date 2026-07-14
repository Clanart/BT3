# Q1628: add spend bundles allow replay across contexts via compressed spend bundle backrefs

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `add_spend_bundles` in `crates/chia-consensus/src/build_interned_block.rs` with compressed spend bundle backrefs when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that fast-forward output preserves singleton lineage and puzzle hash, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:136` / `add_spend_bundles`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: compressed spend bundle backrefs
- Exploit idea: Drive `add_spend_bundles` through its public caller path using compressed spend bundle backrefs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: fast-forward output preserves singleton lineage and puzzle hash
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: construct compressed and uncompressed equivalents and compare additions/removals.
