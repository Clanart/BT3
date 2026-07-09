# Q462: NEAR normalized amount helpers fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public sign/finalize/claim paths across heterogeneous decimals` with control over amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches and desynchronize `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` and the adjacent the next module that consumes the same asset or transfer id after every branch.
