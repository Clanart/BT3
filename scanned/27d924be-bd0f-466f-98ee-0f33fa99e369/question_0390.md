# Q390: is challenge block reuse stale verification state via unfinished block payloads

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `is_challenge_block` in `crates/chia-protocol/src/block_record.rs` with unfinished block payloads when the attacker can choose ordering inside a batch make chia_rs reuse stale verification state, violating the invariant that coin and block hashes bind every consensus-critical field, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/block_record.rs:70` / `is_challenge_block`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `is_challenge_block` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: coin and block hashes bind every consensus-critical field
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: mutate each serialized field and assert hash or validation changes.
