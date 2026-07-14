# Q2764: add zeros collapse distinct inputs into one accepted state via PyO3 object extraction values

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `_add_zeros` in `wheel/python/chia_rs/sized_bytes.py` with PyO3 object extraction values when the same payload is parsed through public bindings make chia_rs collapse distinct inputs into one accepted state, violating the invariant that exceptions cannot be converted into valid outputs, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/python/chia_rs/sized_bytes.py:40` / `_add_zeros`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: PyO3 object extraction values
- Exploit idea: Drive `_add_zeros` through its public caller path using PyO3 object extraction values; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: exceptions cannot be converted into valid outputs
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: round-trip bytes and JSON through bindings and assert canonical equality.
