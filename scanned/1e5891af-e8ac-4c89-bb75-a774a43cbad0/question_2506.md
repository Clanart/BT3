# Q2506: curry tree hash accept invalid consensus data via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with royalty and settlement puzzle fields when the payload is accepted by one public API before another validates it make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:29` / `curry_tree_hash`
- Entrypoint: parse puzzle solution structures
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `curry_tree_hash` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
