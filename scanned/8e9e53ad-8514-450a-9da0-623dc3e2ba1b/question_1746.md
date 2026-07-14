# Q1746: PublicKey treat malformed data as a valid empty/default value via public key and signature byte encodings

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `PublicKey` in `crates/chia-bls/src/public_key.rs` with public key and signature byte encodings when equivalent-looking encodings are mixed make chia_rs treat malformed data as a valid empty/default value, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:25` / `PublicKey`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: public key and signature byte encodings
- Exploit idea: Drive `PublicKey` through its public caller path using public key and signature byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
