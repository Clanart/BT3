# Q3627: new treat malformed data as a valid empty/default value via trusted vs untrusted parse mode inputs

## Question
Can an unprivileged attacker relay network payload bytes through streamable decoding targeting `new` in `crates/chia-protocol/src/bytes.rs` with trusted vs untrusted parse mode inputs at a fork-height or boundary-value activation point make chia_rs treat malformed data as a valid empty/default value, violating the invariant that JSON conversions cannot create impossible protocol states, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-protocol/src/bytes.rs:193` / `new`
- Entrypoint: relay network payload bytes through streamable decoding
- Attacker controls: trusted vs untrusted parse mode inputs
- Exploit idea: Drive `new` through its public caller path using trusted vs untrusted parse mode inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: JSON conversions cannot create impossible protocol states
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trailing consensus bytes never produce a valid object.
