# Q2792: aggregate verify allow replay across contexts via from bytes/from json dict inputs

## Question
Can an unprivileged attacker call the public Python API targeting `aggregate_verify` in `wheel/src/api.rs` with from_bytes/from_json_dict inputs when serialized bytes are validly framed but semantically adversarial make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `wheel/src/api.rs:350` / `aggregate_verify`
- Entrypoint: call the public Python API
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `aggregate_verify` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
