# Q2526: curry tree hash treat malformed data as a valid empty/default value via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with CAT/NFT/DID/offer/singleton puzzle arguments with default-enabled consensus flags make chia_rs treat malformed data as a valid empty/default value, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:30` / `curry_tree_hash`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `curry_tree_hash` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
