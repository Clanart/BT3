# Q367: Native Balance Theft After Partial State Change

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and replaying the same public flow after one part of storage changed and another part did not, and make `dispatch` spend native value already sitting in the dispatcher through an arbitrary public call array so `the dispatcher's native balance` becomes inconsistent with `only value belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Spend native value already sitting in the dispatcher through an arbitrary public call array. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Preload the dispatcher with native value, call dispatch from an arbitrary account, and assert the caller cannot route that value to an attacker-chosen contract. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
