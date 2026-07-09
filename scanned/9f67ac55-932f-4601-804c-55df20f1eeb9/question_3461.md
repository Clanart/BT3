# Q3461: NEAR claim_fee callback fast path and normal path can both pay through cross-module drift

## Question
Can an unprivileged attacker use `proof callback reached from public `claim_fee`` with control over decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals and desynchronize `near/omni-bridge/src/lib.rs::claim_fee_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path and normal path can both pay` attack class because removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::claim_fee_callback` and the adjacent replay-protection bookkeeping after every branch.
