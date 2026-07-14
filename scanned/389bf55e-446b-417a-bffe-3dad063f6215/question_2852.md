# Q2852: FromJsonDict allow replay across contexts via trusted parse flags

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `FromJsonDict` in `crates/chia-traits/src/from_json_dict.rs` with trusted parse flags with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/from_json_dict.rs:7` / `FromJsonDict`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: trusted parse flags
- Exploit idea: Drive `FromJsonDict` through its public caller path using trusted parse flags; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
