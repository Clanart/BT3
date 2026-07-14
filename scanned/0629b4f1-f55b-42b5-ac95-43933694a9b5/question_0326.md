# Q326: hash to g2 derive a different canonical hash via aggregate signature participant lists

## Question
Can an unprivileged attacker provide serialized public keys/signatures targeting `hash_to_g2` in `crates/chia-bls/src/signature.rs` with aggregate signature participant lists when equivalent-looking encodings are mixed make chia_rs derive a different canonical hash, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/signature.rs:311` / `hash_to_g2`
- Entrypoint: provide serialized public keys/signatures
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `hash_to_g2` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare aggregate_verify with independent pairings.
