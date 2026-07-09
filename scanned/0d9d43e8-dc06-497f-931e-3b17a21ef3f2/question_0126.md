# Q126: NEAR normalized amount helpers fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public sign/finalize/claim paths across heterogeneous decimals` with crafted amount, fee, or native-fee inputs and make `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` use inconsistent fee and principal values across normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
