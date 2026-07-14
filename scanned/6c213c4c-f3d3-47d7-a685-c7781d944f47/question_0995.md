# Q995: curry genesis by coin id allow replay across contexts via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `curry_genesis_by_coin_id` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with royalty and settlement puzzle fields with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:163` / `curry_genesis_by_coin_id`
- Entrypoint: parse puzzle solution structures
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `curry_genesis_by_coin_id` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
