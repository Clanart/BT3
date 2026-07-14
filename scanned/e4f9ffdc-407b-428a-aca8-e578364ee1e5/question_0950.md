# Q950: new derive a different canonical hash via improper list terminators

## Question
Can an unprivileged attacker serialize typed values back into CLVM targeting `new` in `crates/clvm-utils/src/tree_hash.rs` with improper list terminators when values sit exactly at max/min integer boundaries make chia_rs derive a different canonical hash, violating the invariant that CLVM atom encodings have canonical typed meanings, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/clvm-utils/src/tree_hash.rs:13` / `new`
- Entrypoint: serialize typed values back into CLVM
- Attacker controls: improper list terminators
- Exploit idea: Drive `new` through its public caller path using improper list terminators; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: CLVM atom encodings have canonical typed meanings
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip ToClvm/FromClvm and compare tree hashes.
