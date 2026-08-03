# Q373: Approval-Powered Drain Across Mixed Context

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `dispatch` abuse existing token approvals held by the dispatcher to move more value than the current flow intended so `the token allowance context held by the dispatcher` becomes inconsistent with `only approvals intentionally consumed by the active protocol flow`, breaking the invariant that any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Abuse existing token approvals held by the dispatcher to move more value than the current flow intended. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: any preapproved token path reachable by the dispatcher must remain scoped to the active protocol call and not to arbitrary external callers
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Give the dispatcher an approval in a test, then call arbitrary transferFrom-style payloads through dispatch and assert unauthorized callers cannot consume it. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
