# Q2181: NewCompactVDF commit output after an error path via JSON dict conversion values

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `NewCompactVDF` in `crates/chia-protocol/src/full_node_protocol.rs` with JSON dict conversion values when equivalent-looking encodings are mixed make chia_rs commit output after an error path, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/full_node_protocol.rs:137` / `NewCompactVDF`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: JSON dict conversion values
- Exploit idea: Drive `NewCompactVDF` through its public caller path using JSON dict conversion values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip bytes through parse/stream/hash in trusted and untrusted modes.
