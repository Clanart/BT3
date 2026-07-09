# Q3731: NEAR claim_fee callback stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `proof callback reached from public `claim_fee`` and make `near/omni-bridge/src/lib.rs::claim_fee_callback` treat it as fresh because of removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
