# Q1403: extract allow replay across contexts via JSON dictionary values

## Question
Can an unprivileged attacker compute streamable hashes targeting `extract` in `crates/chia_py_streamable_macro/src/lib.rs` with JSON dictionary values when a node processes data from an untrusted peer or wallet make chia_rs allow replay across contexts, violating the invariant that hashes commit to vector lengths and enum discriminants, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:61` / `extract`
- Entrypoint: compute streamable hashes
- Attacker controls: JSON dictionary values
- Exploit idea: Drive `extract` through its public caller path using JSON dictionary values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: hashes commit to vector lengths and enum discriminants
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
