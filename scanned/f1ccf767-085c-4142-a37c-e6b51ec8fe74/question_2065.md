# Q2065: get_account_tx_common_fields nonce replay window in execution/transaction_impls.cairo (mode/version split)

## Question
Can a normal Starknet user submitting invoke, declare, deploy_account, or L1-handler-triggering inputs use transaction calldata, nonce selection, resource bounds and tip, signature material accepted by the sender's own account contract, gas/resource edge cases to make `get_account_tx_common_fields` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo` observe or mutate nonce state in an order that lets one accepted user action replay, skip, or double-advance a sender nonce around account nonce and replay protection, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches direct loss of funds? Can validate/execute mode, deprecated/new code paths, or output-mode switches make honest executions disagree on the same public input?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo:170 :: get_account_tx_common_fields
- Entrypoint: normal Starknet user submitting invoke, declare, deploy_account, or L1-handler-triggering inputs
- Attacker controls: transaction calldata, nonce selection, resource bounds and tip, signature material accepted by the sender's own account contract, gas/resource edge cases
- Exploit idea: make the nonce that authorizes the action diverge from the nonce that is later committed or exposed to nested execution while this function is handling account nonce and replay protection. Exploit a mode, version, or output-format split so one phase authorizes a behavior that another phase commits differently.
- Invariant to test: each accepted transaction-like action must consume exactly one sender nonce once, and a reverted or nested path must not leave behind a replayable nonce state All supported modes and versions must preserve one canonical authorization and commitment result for the same accepted public input.
- Expected bounty impact: Direct loss of funds
- Fast validation: exercise nested calls, meta-tx paths, and revert edges around this function, then assert no accepted trace can replay the same logical authorization or strand an account behind an unexpected nonce Cross-test the old/new or validate/execute variants with the same public input and assert they cannot commit divergent results.
