# Q93: new treat malformed data as a valid empty/default value via compressed spend bundle backrefs

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `new` in `crates/chia-consensus/src/build_compressed_block.rs` with compressed spend bundle backrefs when the payload is accepted by one public API before another validates it make chia_rs treat malformed data as a valid empty/default value, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/build_compressed_block.rs:71` / `new`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: compressed spend bundle backrefs
- Exploit idea: Drive `new` through its public caller path using compressed spend bundle backrefs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: construct compressed and uncompressed equivalents and compare additions/removals.
