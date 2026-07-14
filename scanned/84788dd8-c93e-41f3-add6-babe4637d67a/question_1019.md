# Q1019: to clvm allow replay across contexts via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker parse puzzle solution structures targeting `to_clvm` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with royalty and settlement puzzle fields when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:251` / `to_clvm`
- Entrypoint: parse puzzle solution structures
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `to_clvm` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
