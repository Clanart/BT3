# Q1331: FromJsonDict allow replay across contexts via JSON dictionary values

## Question
Can an unprivileged attacker compute streamable hashes targeting `FromJsonDict` in `crates/chia-traits/src/from_json_dict.rs` with JSON dictionary values with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-traits/src/from_json_dict.rs:7` / `FromJsonDict`
- Entrypoint: compute streamable hashes
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `FromJsonDict` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz JSON dictionaries and assert impossible states are rejected.
