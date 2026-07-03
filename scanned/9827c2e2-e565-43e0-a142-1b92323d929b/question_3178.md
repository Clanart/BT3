# Q3178: check_proof_facts attacker-driven shutdown path in execution/execution_constraints__virtual.cairo (mode/version split)

## Question
Can a normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts use proof_facts payload to make `check_proof_facts` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo` use valid attacker-controlled input to drive this path into deterministic aborts or unresolvable disagreement that stop honest nodes from confirming new transactions around proof-fact acceptance, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches unintended chain split / network partition? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execution_constraints__virtual.cairo:14 :: check_proof_facts
- Entrypoint: normal Starknet user submitting an invoke transaction with attacker-chosen proof_facts
- Attacker controls: proof_facts payload
- Exploit idea: find an input shape that turns a local assertion, ordering assumption, or hinted value dependency into a protocol-wide confirmation failure while this function is handling proof-fact acceptance. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: public StarkNet OS inputs must not give an unprivileged attacker a deterministic way to halt confirmation or crash honest execution for otherwise valid blocks All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Unintended chain split / network partition
- Fast validation: fuzz adversarial but valid public inputs that maximize this function's structural edge cases, then assert honest nodes either all reject before block inclusion or all keep confirming transactions Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
