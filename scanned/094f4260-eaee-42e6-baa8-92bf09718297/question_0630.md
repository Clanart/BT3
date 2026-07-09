# Q630: NEAR normalized amount helpers fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public sign/finalize/claim paths across heterogeneous decimals` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` violate `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party` in the `fee and principal split divergence` attack class because normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
