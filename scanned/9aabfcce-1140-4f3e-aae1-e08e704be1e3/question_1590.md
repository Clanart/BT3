# Q1590: from parent treat malformed data as a valid empty/default value via malformed CLVM condition atoms

## Question
Can an unprivileged attacker feed a malicious CLVM spend output into condition parsing targeting `from_parent` in `crates/chia-consensus/src/owned_conditions.rs` with malformed CLVM condition atoms when the attacker can choose ordering inside a batch make chia_rs treat malformed data as a valid empty/default value, violating the invariant that amounts and coin ids remain canonical after sanitization, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/owned_conditions.rs:201` / `from_parent`
- Entrypoint: feed a malicious CLVM spend output into condition parsing
- Attacker controls: malformed CLVM condition atoms
- Exploit idea: Drive `from_parent` through its public caller path using malformed CLVM condition atoms; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: amounts and coin ids remain canonical after sanitization
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz condition atoms and assert validation never accepts the forbidden spend.
