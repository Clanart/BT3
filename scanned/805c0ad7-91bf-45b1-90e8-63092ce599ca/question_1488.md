# Q1488: check nil commit output after an error path via consensus constants at activation boundaries

## Question
Can an unprivileged attacker submit a boundary block/spend sequence targeting `check_nil` in `crates/chia-consensus/src/validation_error.rs` with consensus constants at activation boundaries when the attacker can choose ordering inside a batch make chia_rs commit output after an error path, violating the invariant that block context remains deterministic, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-consensus/src/validation_error.rs:415` / `check_nil`
- Entrypoint: submit a boundary block/spend sequence
- Attacker controls: consensus constants at activation boundaries
- Exploit idea: Drive `check_nil` through its public caller path using consensus constants at activation boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: block context remains deterministic
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: property-test height/seconds constraints against modeled CoinRecord birth data.
