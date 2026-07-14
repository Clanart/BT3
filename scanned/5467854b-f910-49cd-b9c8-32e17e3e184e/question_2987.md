# Q2987: interned vbytes derive a different canonical hash via consensus constants at activation boundaries

## Question
Can an unprivileged attacker replay validation with alternate consensus flags targeting `interned_vbytes` in `crates/chia-consensus/src/generator_cost.rs` with consensus constants at activation boundaries when the same payload is parsed through public bindings make chia_rs derive a different canonical hash, violating the invariant that fork flags cannot make the same input validate differently across honest nodes, and realistically causing the in-scope impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees?

## Target
- File/function: `crates/chia-consensus/src/generator_cost.rs:20` / `interned_vbytes`
- Entrypoint: replay validation with alternate consensus flags
- Attacker controls: consensus constants at activation boundaries
- Exploit idea: Drive `interned_vbytes` through its public caller path using consensus constants at activation boundaries; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: fork flags cannot make the same input validate differently across honest nodes
- Expected Immunefi impact: Critical. Consensus-valid spend, block generator, proof, or block record path can mint, burn, steal, double-spend, or mis-account Chia coins/rewards/fees
- Fast validation: replay identical input twice and assert identical errors and outputs.
