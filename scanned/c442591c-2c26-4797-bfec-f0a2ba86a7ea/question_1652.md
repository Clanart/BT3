# Q1652: check generator node allow replay across contexts via compressed spend bundle backrefs

## Question
Can an unprivileged attacker build a compressed block from user-controlled spend bundles targeting `check_generator_node` in `crates/chia-consensus/src/run_block_generator.rs` with compressed spend bundle backrefs at a fork-height or boundary-value activation point make chia_rs allow replay across contexts, violating the invariant that fast-forward output preserves singleton lineage and puzzle hash, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/run_block_generator.rs:184` / `check_generator_node`
- Entrypoint: build a compressed block from user-controlled spend bundles
- Attacker controls: compressed spend bundle backrefs
- Exploit idea: Drive `check_generator_node` through its public caller path using compressed spend bundle backrefs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: fast-forward output preserves singleton lineage and puzzle hash
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: property-test cost_left never underflows and accepted output stays within limits.
