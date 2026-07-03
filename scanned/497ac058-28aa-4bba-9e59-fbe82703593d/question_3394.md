# Q3394: check_is_reverted block-hash window mismatch in execution/execution_constraints.cairo (mode/version split)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_is_reverted` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo` use boundary block numbers or guessed old-hash values so one honest execution treats a block-hash read as valid while another treats it as stale or unverified around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints.cairo:20 :: check_is_reverted
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: exploit the stored block-hash buffer, guessed header fields, or block-hash mapping path to desynchronize honest views of the same block context while this function is handling proof-fact acceptance. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: all honest nodes and provers must agree on which historical block hash a given accepted input is allowed to read or prove against All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: exercise block numbers at the storage-buffer edge through this function and assert all honest executions agree on acceptance, returned hash, and committed mapping state Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
