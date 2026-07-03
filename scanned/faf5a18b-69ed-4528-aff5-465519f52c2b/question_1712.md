# Q1712: check_is_reverted proof-fact binding gap in execution/execution_constraints.cairo (mode/version split)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_is_reverted` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo` accept attacker-supplied proof_facts that are valid under one base block/config but are consumed as if they authorize another state or block context around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches permanent freezing of funds? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo:20 :: check_is_reverted
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: break the binding between the invoke transaction, virtual OS header, stored block hash, and OS config hash while this function is handling proof-fact acceptance. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: proof-backed transactions must only be accepted when the proof header, stored base block hash, and OS config hash all bind to the same authorized context All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Permanent freezing of funds
- Fast validation: craft proof_facts around boundary block numbers, alternate program hashes, and stale config hashes through this function, then assert no accepted proof can bind to the wrong base block or config Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
