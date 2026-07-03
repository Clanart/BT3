# Q359: check_proof_facts message replay or skip in execution/execution_constraints__virtual.cairo (mode/version split)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_proof_facts` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo` consume or emit an L1/L2 message under a key, payload, or ordering that is not the same one earlier checked or later committed around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches permanent freezing of funds? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo:14 :: check_proof_facts
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: make message uniqueness depend on one header/payload view while output serialization or consumption uses another while this function is handling proof-fact acceptance. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: a message must be consumed or emitted exactly once under one canonical header/payload hash and must not be skipped, duplicated, or rebound to a different destination All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Permanent freezing of funds
- Fast validation: exercise attacker-controlled payload lengths, nested calls, and revert edges through this function, then assert the message ledger/output contains exactly one canonical effect per accepted message Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
