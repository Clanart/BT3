# Q1021: new accept invalid consensus data via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker derive puzzle tree hashes targeting `new` in `crates/chia-puzzle-types/src/puzzles/offer.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/offer.rs:15` / `new`
- Entrypoint: derive puzzle tree hashes
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `new` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
