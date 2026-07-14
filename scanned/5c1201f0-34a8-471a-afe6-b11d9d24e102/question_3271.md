# Q3271: from integer accept invalid consensus data via duplicate public-key/message pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_integer` in `crates/chia-bls/src/public_key.rs` with duplicate public-key/message pairs when equivalent-looking encodings are mixed make chia_rs accept invalid consensus data, violating the invariant that aggregate signatures prove every required message/public-key pair, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/public_key.rs:78` / `from_integer`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `from_integer` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: aggregate signatures prove every required message/public-key pair
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
