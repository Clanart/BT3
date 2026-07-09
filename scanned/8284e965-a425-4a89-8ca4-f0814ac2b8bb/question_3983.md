# Q3983: NEAR claim_fee callback stale or reordered proof acceptance through cross-module drift

## Question
Can an unprivileged attacker use `proof callback reached from public `claim_fee`` with control over decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals and desynchronize `near/omni-bridge/src/lib.rs::claim_fee_callback` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `stale or reordered proof acceptance` attack class because removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::claim_fee_callback` and the adjacent replay-protection bookkeeping after every branch.
