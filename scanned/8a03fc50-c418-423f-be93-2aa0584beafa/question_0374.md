# Q374: Approval-Powered Drain With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `dispatch` abuse existing token approvals held by the dispatcher to move more value than the current flow intended so `the token allowance context held by the dispatcher` becomes inconsistent with `only approvals intentionally consumed by the active protocol flow`, breaking the invariant that any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Abuse existing token approvals held by the dispatcher to move more value than the current flow intended. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Give the dispatcher an approval in a test, then call arbitrary transferFrom-style payloads through dispatch and assert unauthorized callers cannot consume it. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
