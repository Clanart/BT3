# Q370: ERC20 Balance Theft With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `dispatch` transfer ERC20 balances already sitting in the dispatcher through arbitrary call data so `the dispatcher's ERC20 balances` becomes inconsistent with `only tokens belonging to the currently authorized protocol flow`, breaking the invariant that a public dispatcher must not let arbitrary callers move stored ERC20 balances that belong to other flows or users and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Transfer erc20 balances already sitting in the dispatcher through arbitrary call data. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: a public dispatcher must not let arbitrary callers move stored ERC20 balances that belong to other flows or users
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Leave ERC20 balance on the dispatcher, then call dispatch as an arbitrary user and assert the user cannot force a transfer to self. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
