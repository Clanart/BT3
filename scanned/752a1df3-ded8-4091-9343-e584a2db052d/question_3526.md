# Q3526: into inner mis-bind attacker-controlled bytes to trusted state via unfinished block payloads

## Question
Can an unprivileged attacker parse and relay serialized protocol objects targeting `into_inner` in `crates/chia-protocol/src/program.rs` with unfinished block payloads when the same payload is parsed through public bindings make chia_rs mis-bind attacker-controlled bytes to trusted state, violating the invariant that coin and block hashes bind every consensus-critical field, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/program.rs:66` / `into_inner`
- Entrypoint: parse and relay serialized protocol objects
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `into_inner` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: coin and block hashes bind every consensus-critical field
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
