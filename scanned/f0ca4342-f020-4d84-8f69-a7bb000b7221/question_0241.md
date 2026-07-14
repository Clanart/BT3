# Q241: update digest accept invalid consensus data via public key and signature byte encodings

## Question
Can an unprivileged attacker submit aggregate signature material targeting `update_digest` in `crates/chia-bls/src/public_key.rs` with public key and signature byte encodings at a fork-height or boundary-value activation point make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:190` / `update_digest`
- Entrypoint: submit aggregate signature material
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `update_digest` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
