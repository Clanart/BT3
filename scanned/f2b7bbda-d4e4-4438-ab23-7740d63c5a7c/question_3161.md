# Q3161: check_proof_facts config-hash skew in execution/execution_constraints.cairo (mode/version split)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_proof_facts` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo` let attacker-visible output or proof state bind to one StarkNet OS config while execution or downstream verification uses another around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo:34 :: check_proof_facts
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: break the link between the config hash in global context, proof headers, fee token address, and serialized OS output while this function is handling proof-fact acceptance. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: one accepted execution must have exactly one active OS configuration binding across execution, proof validation, and public output All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: vary chain_id, fee token address, public key hash, and proof headers around this function, then assert no accepted trace can mix config values across phases Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
