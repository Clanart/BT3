# Q85: get flags for height and constants accept invalid consensus data via malformed CLVM condition atoms

## Question
Can an unprivileged attacker call the Python validation API with attacker-controlled spends targeting `get_flags_for_height_and_constants` in `crates/chia-consensus/src/spendbundle_validation.rs` with malformed CLVM condition atoms when a node processes data from an untrusted peer or wallet make chia_rs accept invalid consensus data, violating the invariant that only spend conditions satisfying consensus rules are accepted, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/spendbundle_validation.rs:61` / `get_flags_for_height_and_constants`
- Entrypoint: call the Python validation API with attacker-controlled spends
- Attacker controls: malformed CLVM condition atoms
- Exploit idea: Drive `get_flags_for_height_and_constants` through its public caller path using malformed CLVM condition atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: only spend conditions satisfying consensus rules are accepted
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: build a generator with the condition variant and assert the exact ErrorCode or accepted SpendBundleConditions.
