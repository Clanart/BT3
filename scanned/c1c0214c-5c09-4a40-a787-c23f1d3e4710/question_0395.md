# Q395: py is challenge block allow replay across contexts via reward-chain and foliage fields

## Question
Can an unprivileged attacker submit serialized block or spend data targeting `py_is_challenge_block` in `crates/chia-protocol/src/block_record.rs` with reward-chain and foliage fields when the attacker can choose ordering inside a batch make chia_rs allow replay across contexts, violating the invariant that state transitions preserve parent-child coin relationships, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-protocol/src/block_record.rs:109` / `py_is_challenge_block`
- Entrypoint: submit serialized block or spend data
- Attacker controls: reward-chain and foliage fields
- Exploit idea: Drive `py_is_challenge_block` through its public caller path using reward-chain and foliage fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: state transitions preserve parent-child coin relationships
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: compare Rust and Python object construction from the same bytes.
