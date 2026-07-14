# Q3384: from json dict reuse stale verification state via aggregate signature participant lists

## Question
Can an unprivileged attacker verify signatures through consensus or binding APIs targeting `from_json_dict` in `crates/chia-bls/src/signature.rs` with aggregate signature participant lists when equivalent-looking encodings are mixed make chia_rs reuse stale verification state, violating the invariant that invalid, infinity, or subgroup-edge keys cannot authorize spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/signature.rs:570` / `from_json_dict`
- Entrypoint: verify signatures through consensus or binding APIs
- Attacker controls: aggregate signature participant lists
- Exploit idea: Drive `from_json_dict` through its public caller path using aggregate signature participant lists; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: invalid, infinity, or subgroup-edge keys cannot authorize spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz serialized keys/signatures and assert invalid encodings are rejected.
