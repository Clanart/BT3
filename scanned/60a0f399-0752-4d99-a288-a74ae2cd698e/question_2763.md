# Q2763: repr reuse stale verification state via run generator API arguments

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `__repr__` in `wheel/python/chia_rs/sized_byte_class.py` with run_generator API arguments when the same payload is parsed through public bindings make chia_rs reuse stale verification state, violating the invariant that buffers are copied or borrowed without stale mutation changing validation, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/python/chia_rs/sized_byte_class.py:83` / `__repr__`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: run_generator API arguments
- Exploit idea: Drive `__repr__` through its public caller path using run_generator API arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: buffers are copied or borrowed without stale mutation changing validation
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
