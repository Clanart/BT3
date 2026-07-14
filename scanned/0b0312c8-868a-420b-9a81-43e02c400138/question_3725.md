# Q3725: update digest allow replay across contexts via streamable byte prefixes and trailing bytes

## Question
Can an unprivileged attacker parse untrusted streamable bytes targeting `update_digest` in `crates/chia-protocol/src/utils.rs` with streamable byte prefixes and trailing bytes with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that trusted parsing cannot be reached for attacker-controlled invalid bytes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/utils.rs:5` / `update_digest`
- Entrypoint: parse untrusted streamable bytes
- Attacker controls: streamable byte prefixes and trailing bytes
- Exploit idea: Drive `update_digest` through its public caller path using streamable byte prefixes and trailing bytes; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parsing cannot be reached for attacker-controlled invalid bytes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: differential-test JSON dict conversion against streamable bytes.
