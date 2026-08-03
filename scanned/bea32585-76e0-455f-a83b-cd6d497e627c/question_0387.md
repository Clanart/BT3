# Q387: Cross-User Dust Theft After Partial State Change

## Question
Can an unprivileged attacker enter through `CallDispatcher.dispatch(encodedCalls)` with attacker-controlled arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals and replaying the same public flow after one part of storage changed and another part did not, and make `dispatch` claim leftover balances, dust, or sweepable assets that an earlier user flow left behind so `the dust and residual balances held by the dispatcher` becomes inconsistent with `only the user or protocol flow that created those residual balances`, breaking the invariant that residual balances created by one user flow must not be claimable by another arbitrary external caller and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: evm/src/utils/CallDispatcher.sol::dispatch
- Entrypoint: CallDispatcher.dispatch(encodedCalls)
- Attacker controls: arbitrary call arrays, target addresses, calldata, call values, and any residual dispatcher balances or approvals
- Exploit idea: Claim leftover balances, dust, or sweepable assets that an earlier user flow left behind. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: residual balances created by one user flow must not be claimable by another arbitrary external caller
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Leave dust behind from one flow, call dispatch from another account, and assert the second account cannot sweep the first flow's leftovers. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
