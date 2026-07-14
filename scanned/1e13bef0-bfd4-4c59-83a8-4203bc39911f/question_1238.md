# Q1238: from hexstr derive a different canonical hash via Python lists of tuple spend inputs

## Question
Can an unprivileged attacker round-trip objects through bytes and JSON targeting `from_hexstr` in `wheel/python/chia_rs/sized_byte_class.py` with Python lists of tuple spend inputs when the same payload is parsed through public bindings make chia_rs derive a different canonical hash, violating the invariant that Python inputs produce the same result as Rust consensus code, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `wheel/python/chia_rs/sized_byte_class.py:60` / `from_hexstr`
- Entrypoint: round-trip objects through bytes and JSON
- Attacker controls: Python lists of tuple spend inputs
- Exploit idea: Drive `from_hexstr` through its public caller path using Python lists of tuple spend inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: Python inputs produce the same result as Rust consensus code
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Python and Rust validation for the same serialized spend bundle.
