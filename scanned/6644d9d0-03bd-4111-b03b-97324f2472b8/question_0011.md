# Q11: new spend allow replay across contexts via CREATE COIN outputs with edge-case amounts and hints

## Question
Can an unprivileged attacker include a spend in a block generator targeting `new_spend` in `crates/chia-consensus/src/conditions.rs` with CREATE_COIN outputs with edge-case amounts and hints with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that mempool and block validation agree on condition semantics, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-consensus/src/conditions.rs:69` / `new_spend`
- Entrypoint: include a spend in a block generator
- Attacker controls: CREATE_COIN outputs with edge-case amounts and hints
- Exploit idea: Drive `new_spend` through its public caller path using CREATE_COIN outputs with edge-case amounts and hints; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: mempool and block validation agree on condition semantics
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: fuzz condition atoms and assert validation never accepts the forbidden spend.
