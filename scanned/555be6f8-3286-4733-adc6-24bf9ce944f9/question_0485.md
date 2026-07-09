# Q485: NEAR fast transfer status queries fast-transfer status changes in the wrong order through cross-module drift

## Question
Can an unprivileged attacker use `public relayer-facing reads consumed by off-chain automation` with control over fast-transfer id choice and timing relative to claims or callbacks and desynchronize `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast-transfer status changes in the wrong order` attack class because exposes whether a fast transfer exists and whether it is marked finalised, violating `observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised`
- Entrypoint: `public relayer-facing reads consumed by off-chain automation`
- Attacker controls: fast-transfer id choice and timing relative to claims or callbacks
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: observable fast-transfer state must correspond to one unambiguous economic state so relayers cannot act on stale statuses that the contract later interprets differently
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_fast_transfer_status / is_fast_transfer_finalised` and the adjacent replay-protection bookkeeping after every branch.
