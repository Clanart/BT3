# Q2000: new allow replay across contexts via CoinState/CoinRecord transition sequences

## Question
Can an unprivileged attacker parse and relay serialized protocol objects targeting `new` in `crates/chia-protocol/src/program.rs` with CoinState/CoinRecord transition sequences when the same payload is parsed through public bindings make chia_rs allow replay across contexts, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/program.rs:46` / `new`
- Entrypoint: parse and relay serialized protocol objects
- Attacker controls: CoinState/CoinRecord transition sequences
- Exploit idea: Drive `new` through its public caller path using CoinState/CoinRecord transition sequences; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: parse-stream-hash round-trip the object and compare field hashes.
