# Q1997: py is transaction block overflow or underflow a boundary check via unfinished block payloads

## Question
Can an unprivileged attacker process network-delivered protocol payloads targeting `py_is_transaction_block` in `crates/chia-protocol/src/header_block.rs` with unfinished block payloads when the same payload is parsed through public bindings make chia_rs overflow or underflow a boundary check, violating the invariant that trusted and untrusted parsing cannot disagree on valid network bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/header_block.rs:142` / `py_is_transaction_block`
- Entrypoint: process network-delivered protocol payloads
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `py_is_transaction_block` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted and untrusted parsing cannot disagree on valid network bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
