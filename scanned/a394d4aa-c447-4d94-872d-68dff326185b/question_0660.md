# Q660: NewCompactVDF commit output after an error path via network message payload bytes

## Question
Can an unprivileged attacker convert JSON dict values into protocol structs targeting `NewCompactVDF` in `crates/chia-protocol/src/full_node_protocol.rs` with network message payload bytes when equivalent-looking encodings are mixed make chia_rs commit output after an error path, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/full_node_protocol.rs:137` / `NewCompactVDF`
- Entrypoint: convert JSON dict values into protocol structs
- Attacker controls: network message payload bytes
- Exploit idea: Drive `NewCompactVDF` through its public caller path using network message payload bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
