# Q746: py get string derive a different canonical hash via VDF/classgroup byte encodings

## Question
Can an unprivileged attacker submit proof and block challenge data targeting `py_get_string` in `crates/chia-protocol/src/partial_proof.rs` with VDF/classgroup byte encodings when a node processes data from an untrusted peer or wallet make chia_rs derive a different canonical hash, violating the invariant that proof quality and iteration calculations are deterministic, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/partial_proof.rs:50` / `py_get_string`
- Entrypoint: submit proof and block challenge data
- Attacker controls: VDF/classgroup byte encodings
- Exploit idea: Drive `py_get_string` through its public caller path using VDF/classgroup byte encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: proof quality and iteration calculations are deterministic
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: test boundary iteration values against a simple arithmetic model.
