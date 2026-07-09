# Q15: NEAR claim_fee callback replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `proof callback reached from public `claim_fee`` and make `near/omni-bridge/src/lib.rs::claim_fee_callback` either bypass replay protection or consume it for the wrong event because of removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
