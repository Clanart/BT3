# Q2524: NftIntermediateLauncherArgs collapse distinct inputs into one accepted state via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `NftIntermediateLauncherArgs` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with royalty and settlement puzzle fields with default-enabled consensus flags make chia_rs collapse distinct inputs into one accepted state, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:15` / `NftIntermediateLauncherArgs`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `NftIntermediateLauncherArgs` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
