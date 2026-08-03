# Q375: Approval-Powered Drain After Partial State Change

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and replaying the same public flow after one part of storage changed and another part did not, and make `dispatch` abuse existing token approvals held by the dispatcher to move more value than the current flow intended so `the token allowance context held by the dispatcher` becomes inconsistent with `only approvals intentionally consumed by the active protocol flow`, breaking the invariant that any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Abuse existing token approvals held by the dispatcher to move more value than the current flow intended. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Give the dispatcher an approval in a test, then call arbitrary transferFrom-style payloads through dispatch and assert unauthorized callers cannot consume it. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
