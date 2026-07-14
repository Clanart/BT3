# Q1299: get plot index skip a required validation guard via from bytes/from json dict inputs

## Question
Can an unprivileged attacker pass attacker-controlled buffers through PyO3 bindings targeting `get_plot_index` in `wheel/src/api.rs` with from_bytes/from_json_dict inputs when the payload is accepted by one public API before another validates it make chia_rs skip a required validation guard, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/api.rs:699` / `get_plot_index`
- Entrypoint: pass attacker-controlled buffers through PyO3 bindings
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `get_plot_index` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
