# Q516: update digest commit output after an error path via unfinished block payloads

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `update_digest` in `crates/chia-protocol/src/reward_chain_block.rs` with unfinished block payloads when values sit exactly at max/min integer boundaries make chia_rs commit output after an error path, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/reward_chain_block.rs:48` / `update_digest`
- Entrypoint: submit serialized block or spend data
- Attacker controls: unfinished block payloads
- Exploit idea: Drive `update_digest` through its public caller path using unfinished block payloads; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: feed trailing and truncated bytes and assert rejection in untrusted mode.
