# Q2525: new overflow or underflow a boundary check via memo and proof structures

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with memo and proof structures with default-enabled consensus flags make chia_rs overflow or underflow a boundary check, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:22` / `new`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: memo and proof structures
- Exploit idea: Drive `new` through its public caller path using memo and proof structures; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
