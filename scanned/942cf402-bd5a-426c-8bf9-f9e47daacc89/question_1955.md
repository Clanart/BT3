# Q1955: py is transaction block derive a different canonical hash via unfinished block payloads

## Question
Can an unprivileged attacker round-trip protocol objects through Rust/Python APIs targeting `py_is_transaction_block` in `crates/chia-protocol/src/fullblock.rs` with unfinished block payloads when the payload is accepted by one public API before another validates it make chia_rs derive a different canonical hash, violating the invariant that serialized consensus objects have one canonical meaning, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/fullblock.rs:263` / `py_is_transaction_block`
- Entrypoint: round-trip protocol objects through Rust/Python APIs
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `py_is_transaction_block` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: serialized consensus objects have one canonical meaning
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
