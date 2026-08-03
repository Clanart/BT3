# Q369: ERC20 Balance Theft Across Mixed Context

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and mixing bytes that were valid in one proof, chain, module, order, or beneficiary context with metadata interpreted in another context, and make `dispatch` transfer ERC20 balances already sitting in the dispatcher through arbitrary call data so `the dispatcher's ERC20 balances` becomes inconsistent with `only tokens belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move stored ERC20 balances that belong to other flows or users and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Transfer erc20 balances already sitting in the dispatcher through arbitrary call data. Try a pair of otherwise valid artifacts where one verification step authenticates the old context and a later step consumes the new context.
- Invariant to test: a public dispatcher must not let arbitrary callers move stored ERC20 balances that belong to other flows or users
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Leave ERC20 balance on the dispatcher, then call dispatch as an arbitrary user and assert the user cannot force a transfer to self. Build two neighboring valid contexts and mutate only the binding field while asserting state, receipts, and balances stay unchanged.
