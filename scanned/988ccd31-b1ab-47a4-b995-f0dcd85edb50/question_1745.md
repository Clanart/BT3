# Q1745: parse hex string overflow or underflow a boundary check via secp prehashed message/signature pairs

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `parse_hex_string` in `crates/chia-bls/src/parse_hex.rs` with secp prehashed message/signature pairs when equivalent-looking encodings are mixed make chia_rs overflow or underflow a boundary check, violating the invariant that domain-separated signed messages cannot be replayed, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-bls/src/parse_hex.rs:6` / `parse_hex_string`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: secp prehashed message/signature pairs
- Exploit idea: Drive `parse_hex_string` through its public caller path using secp prehashed message/signature pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: domain-separated signed messages cannot be replayed
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare aggregate_verify with independent pairings.
