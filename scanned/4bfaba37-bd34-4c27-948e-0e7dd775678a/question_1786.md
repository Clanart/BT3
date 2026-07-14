# Q1786: from json dict accept invalid consensus data via unhardened derivation indexes

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_json_dict` in `crates/chia-bls/src/public_key.rs` with unhardened derivation indexes when duplicate or prefix-colliding items are present make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:404` / `from_json_dict`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: unhardened derivation indexes
- Exploit idea: Drive `from_json_dict` through its public caller path using unhardened derivation indexes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
