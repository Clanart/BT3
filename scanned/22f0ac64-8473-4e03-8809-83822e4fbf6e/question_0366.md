# Q366: Native Balance Theft With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `dispatch` spend native value already sitting in the dispatcher through an arbitrary public call array so `the dispatcher's native balance` becomes inconsistent with `only value belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Spend native value already sitting in the dispatcher through an arbitrary public call array. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Preload the dispatcher with native value, call dispatch from an arbitrary account, and assert the caller cannot route that value to an attacker-chosen contract. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
