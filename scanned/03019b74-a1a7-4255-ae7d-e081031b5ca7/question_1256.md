# Q1256: confirm included already hashed overflow or underflow a boundary check via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker invoke validation helpers from Python targeting `confirm_included_already_hashed` in `wheel/src/api.rs` with Python lists of tuple spend inputs when serialized bytes are validly framed but semantically adversarial make chia_rs overflow or underflow a boundary check, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:105` / `confirm_included_already_hashed`
- Entrypoint: invoke validation helpers from Python
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `confirm_included_already_hashed` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
