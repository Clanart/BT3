# Q687: NEAR claim_fee callback recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `proof callback reached from public `claim_fee`` that makes `near/omni-bridge/src/lib.rs::claim_fee_callback` settle principal to one party but authorize fee claim or callback routing for another due to removes the stored transfer, enforces fee-recipient equality, reconciles fast-transfer state, denormalizes the amount from the destination event, computes fee including any documented dust, and sends the fee, violating `claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::claim_fee_callback`
- Entrypoint: `proof callback reached from public `claim_fee``
- Attacker controls: decoded `FinTransfer` result, predecessor account, pending transfer record, origin transfer id for fast paths, and token decimals
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: claiming fees must never let a caller delete the pending record, collect twice, or collect against a destination event that does not match the stored origin transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
