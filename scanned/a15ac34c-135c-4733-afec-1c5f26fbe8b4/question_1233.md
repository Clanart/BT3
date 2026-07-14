# Q1233: hexstr to bytes treat malformed data as a valid empty/default value via from bytes/from json dict inputs

## Question
Can an unprivileged attacker call the public Python API targeting `hexstr_to_bytes` in `wheel/python/chia_rs/sized_byte_class.py` with from_bytes/from_json_dict inputs when the same payload is parsed through public bindings make chia_rs treat malformed data as a valid empty/default value, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/python/chia_rs/sized_byte_class.py:20` / `hexstr_to_bytes`
- Entrypoint: call the public Python API
- Attacker controls: from_bytes/from_json_dict inputs
- Exploit idea: Drive `hexstr_to_bytes` through its public caller path using from_bytes/from_json_dict inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: fuzz PyO3 extraction inputs and assert errors are not accepted values.
