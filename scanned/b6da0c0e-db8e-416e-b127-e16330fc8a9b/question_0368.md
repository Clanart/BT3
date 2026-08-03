# Q368: Native Balance Theft By Reusing Data Cross Module

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and reusing data that should belong to one chain, module, order, state machine, or payee in another publicly reachable path, and make `dispatch` spend native value already sitting in the dispatcher through an arbitrary public call array so `the dispatcher's native balance` becomes inconsistent with `only value belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Spend native value already sitting in the dispatcher through an arbitrary public call array. Craft two public flows that share one byte string or hash and check whether module, chain, or payee binding is enforced everywhere.
- Invariant to test: a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Preload the dispatcher with native value, call dispatch from an arbitrary account, and assert the caller cannot route that value to an attacker-chosen contract. Feed the same bytes through two reachable entrypoints and assert the second path rejects instead of inheriting the first path's authorization.
