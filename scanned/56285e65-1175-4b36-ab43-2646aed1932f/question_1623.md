# Q1623: InternedBlockBuilder reuse stale verification state via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker submit a block generator targeting `InternedBlockBuilder` in `crates/chia-consensus/src/build_interned_block.rs` with singleton fast-forward lineage proof fields when the payload is accepted by one public API before another validates it make chia_rs reuse stale verification state, violating the invariant that generator references cannot change spend meaning, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/build_interned_block.rs:51` / `InternedBlockBuilder`
- Entrypoint: submit a block generator
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `InternedBlockBuilder` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generator references cannot change spend meaning
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz generator refs/backrefs and assert deterministic output.
