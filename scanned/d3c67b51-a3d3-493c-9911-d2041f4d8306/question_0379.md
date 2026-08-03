# Q379: Malicious Callback Chain After Partial State Change

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and replaying the same public flow after one part of storage changed and another part did not, and make `dispatch` use one dispatched call to create a second unauthorized state-changing call under the dispatcher's balance or identity so `the call chain executed under dispatcher context` becomes inconsistent with `only the single intended call sequence of the active protocol flow`, breaking the invariant that dispatcher-executed calls must not be chainable into a second unauthorized value-moving or state-changing sequence for arbitrary callers and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Use one dispatched call to create a second unauthorized state-changing call under the dispatcher's balance or identity. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: dispatcher-executed calls must not be chainable into a second unauthorized value-moving or state-changing sequence for arbitrary callers
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Target a contract that reenters or pivots execution and assert the dispatcher cannot be turned into an open execution wallet. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
