# Q1940: NEAR normalized amount helpers one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public sign/finalize/claim paths across heterogeneous decimals` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` violate `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party` in the `one inbound event spawns multiple outbound obligations` attack class because normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
