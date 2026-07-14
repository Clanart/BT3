# Q2825: quality string from proof overflow or underflow a boundary check via cross-language conversion outputs

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `quality_string_from_proof` in `wheel/src/api.rs` with cross-language conversion outputs when a node processes data from an untrusted peer or wallet make chia_rs overflow or underflow a boundary check, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:765` / `quality_string_from_proof`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: cross-language conversion outputs
- Exploit idea: Drive `quality_string_from_proof` through its public caller path using cross-language conversion outputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
