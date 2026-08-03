# Q386: Cross-User Dust Theft With Duplicate Or Reordered Items

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and placing duplicate or reordered leaves, signatures, requests, responses, timeouts, or commitments inside one user-accessible batch, and make `dispatch` claim leftover balances, dust, or sweepable assets that an earlier user flow left behind so `the dust and residual balances held by the dispatcher` becomes inconsistent with `only the user or protocol flow that created those residual balances`, breaking the invariant that residual balances created by one user flow must not be claimable by another arbitrary external caller and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Claim leftover balances, dust, or sweepable assets that an earlier user flow left behind. Use a batch with one honest item and one duplicated or reordered item to see whether unique-item assumptions collapse.
- Invariant to test: residual balances created by one user flow must not be claimable by another arbitrary external caller
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Leave dust behind from one flow, call dispatch from another account, and assert the second account cannot sweep the first flow's leftovers. Write a focused batch test with repeated indices or commitments and assert only unique authenticated items can affect state.
