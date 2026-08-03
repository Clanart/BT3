# Q365: Native Balance Theft Across Mixed Context

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `dispatch` spend native value already sitting in the dispatcher through an arbitrary public call array so `the dispatcher's native balance` becomes inconsistent with `only value belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Spend native value already sitting in the dispatcher through an arbitrary public call array. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: a public dispatcher must not let arbitrary callers move native value that belongs to another user or another protocol flow
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Preload the dispatcher with native value, call dispatch from an arbitrary account, and assert the caller cannot route that value to an attacker-chosen contract. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
