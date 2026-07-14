# Q2542: new accept invalid consensus data via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `new` in `crates/chia-puzzle-types/src/puzzles/offer.rs` with royalty and settlement puzzle fields when the same payload is parsed through public bindings make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/offer.rs:15` / `new`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `new` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: mutate lineage and launcher fields and assert spend rejection.
