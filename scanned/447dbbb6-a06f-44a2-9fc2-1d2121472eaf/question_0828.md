# Q828: VariantInfo commit output after an error path via big integer encodings

## Question
Can an unprivileged attacker derive typed values from CLVM nodes targeting `VariantInfo` in `crates/clvm-derive/src/parser/variant_info.rs` with big integer encodings when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that FromClvm and ToClvm round trips preserve semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/clvm-derive/src/parser/variant_info.rs:5` / `VariantInfo`
- Entrypoint: derive typed values from CLVM nodes
- Attacker controls: big integer encodings
- Exploit idea: Drive `VariantInfo` through its public caller path using big integer encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: FromClvm and ToClvm round trips preserve semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
