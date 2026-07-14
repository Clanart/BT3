# Q1980: prev header hash skip a required validation guard via serialized CoinSpend and SpendBundle objects

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `prev_header_hash` in `crates/chia-protocol/src/header_block.rs` with serialized CoinSpend and SpendBundle objects with default-enabled consensus flags make chia_rs skip a required validation guard, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/header_block.rs:37` / `prev_header_hash`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: serialized CoinSpend and SpendBundle objects
- Exploit idea: Drive `prev_header_hash` through its public caller path using serialized CoinSpend and SpendBundle objects; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
