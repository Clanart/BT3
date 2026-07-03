# Q3583: check_is_reverted config-hash skew in execution/execution_constraints__virtual.cairo (boundary-value edge)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_is_reverted` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo` let attacker-visible output or proof state bind to one StarkNet OS config while execution or downstream verification uses another around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Specifically, can boundary field values, empty/non-empty transitions, or zero-special-case branches trigger the discrepancy?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo:7 :: check_is_reverted
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: break the link between the config hash in global context, proof headers, fee token address, and serialized OS output while this function is handling proof-fact acceptance. Use attacker-chosen boundary values, zero sentinels, and empty/non-empty transitions to hit the sharp branch in this path.
- Invariant to test: one accepted execution must have exactly one active OS configuration binding across execution, proof validation, and public output Boundary values and zero-special-case handling must preserve the same security and accounting invariant as ordinary values.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: vary chain_id, fee token address, public key hash, and proof headers around this function, then assert no accepted trace can mix config values across phases Add a focused fuzz harness around zero, max-sized, empty, and single-element cases and assert the same invariant still holds.
