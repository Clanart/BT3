# Q2988: deploy_contract nonce replay window in execution/deploy_contract.cairo (nested-call revert edge)

## Question
Can a contract deployer or deploy-account sender controlling class hash, salt, constructor calldata, and follow-up calls use transaction calldata, nonce selection, resource bounds and tip, signature material accepted by the sender's own account contract, class hash, constructor calldata to make `deploy_contract` in `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo` observe or mutate nonce state in an order that lets one accepted user action replay, skip, or double-advance a sender nonce around deployment and constructor ordering, so that honest StarkNet OS execution commits, emits, or accepts an attacker-favorable result that reaches direct loss of funds? Can the discrepancy be triggered only after a nested call, constructor call, or inner revert changes ownership of side effects?

## Target
- File/function: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo:29 :: deploy_contract
- Entrypoint: contract deployer or deploy-account sender controlling class hash, salt, constructor calldata, and follow-up calls
- Attacker controls: transaction calldata, nonce selection, resource bounds and tip, signature material accepted by the sender's own account contract, class hash, constructor calldata
- Exploit idea: make the nonce that authorizes the action diverge from the nonce that is later committed or exposed to nested execution while this function is handling deployment and constructor ordering. Drive the bug only after nested execution crosses a caller/callee or revert boundary.
- Invariant to test: each accepted transaction-like action must consume exactly one sender nonce once, and a reverted or nested path must not leave behind a replayable nonce state Nested execution and rollback must preserve the same ownership and rollback invariant as the flat path.
- Expected bounty impact: Direct loss of funds
- Fast validation: exercise nested calls, meta-tx paths, and revert edges around this function, then assert no accepted trace can replay the same logical authorization or strand an account behind an unexpected nonce Construct a nested-call test with mixed success/failure outcomes and assert state, messages, and class changes stay attributable and revertable.
