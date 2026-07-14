# Q3158: fast forward singleton overflow or underflow a boundary check via singleton fast-forward lineage proof fields

## Question
Can an unprivileged attacker submit a block generator targeting `fast_forward_singleton` in `crates/chia-consensus/src/fast_forward.rs` with singleton fast-forward lineage proof fields when equivalent-looking encodings are mixed make chia_rs overflow or underflow a boundary check, violating the invariant that compressed and uncompressed generators produce identical spends, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/fast_forward.rs:59` / `fast_forward_singleton`
- Entrypoint: submit a block generator
- Attacker controls: singleton fast-forward lineage proof fields
- Exploit idea: Drive `fast_forward_singleton` through its public caller path using singleton fast-forward lineage proof fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: compressed and uncompressed generators produce identical spends
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz generator refs/backrefs and assert deterministic output.
