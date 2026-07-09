# Q3141: NEAR normalized amount helpers fee recipient can be substituted or reclaimed by attacker at boundary values

## Question
Can an unprivileged attacker trigger `public sign/finalize/claim paths across heterogeneous decimals` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` violate `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party` in the `fee recipient can be substituted or reclaimed by attacker` attack class because normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
