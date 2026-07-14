# Q2193: TimestampedPeerInfo commit output after an error path via JSON dict conversion values

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `TimestampedPeerInfo` in `crates/chia-protocol/src/peer_info.rs` with JSON dict conversion values with default-enabled consensus flags make chia_rs commit output after an error path, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/peer_info.rs:4` / `TimestampedPeerInfo`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: JSON dict conversion values
- Exploit idea: Drive `TimestampedPeerInfo` through its public caller path using JSON dict conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
