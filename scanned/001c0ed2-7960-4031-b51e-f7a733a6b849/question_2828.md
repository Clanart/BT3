# Q2828: map pyerr allow replay across contexts via from bytes/from json dict inputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `map_pyerr` in `wheel/src/error.rs` with from_bytes/from_json_dict inputs when the payload is accepted by one public API before another validates it make chia_rs allow replay across contexts, violating the invariant that binding conversions preserve canonical bytes and hashes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/src/error.rs:5` / `map_pyerr`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `map_pyerr` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: binding conversions preserve canonical bytes and hashes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
