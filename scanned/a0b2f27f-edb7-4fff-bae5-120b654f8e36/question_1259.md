# Q1259: compute plot id v1 allow replay across contexts via PyO3 object extraction values

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `compute_plot_id_v1` in `wheel/src/api.rs` with PyO3 object extraction values when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:135` / `compute_plot_id_v1`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `compute_plot_id_v1` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
