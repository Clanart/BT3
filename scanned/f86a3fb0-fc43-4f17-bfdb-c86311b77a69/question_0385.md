# Q385: Cross-User Dust Theft Across Mixed Context

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `dispatch` claim leftover balances, dust, or sweepable assets that an earlier user flow left behind so `the dust and residual balances held by the dispatcher` becomes inconsistent with `only the user or protocol flow that created those residual balances`, breaking the invariant that residual balances created by one user flow must not be claimable by another arbitrary external caller and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Claim leftover balances, dust, or sweepable assets that an earlier user flow left behind. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: residual balances created by one user flow must not be claimable by another arbitrary external caller
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Leave dust behind from one flow, call dispatch from another account, and assert the second account cannot sweep the first flow's leftovers. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
