# Q797: NEAR normalized amount helpers recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public sign/finalize/claim paths across heterogeneous decimals` that makes `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` settle principal to one party but authorize fee claim or callback routing for another due to normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
