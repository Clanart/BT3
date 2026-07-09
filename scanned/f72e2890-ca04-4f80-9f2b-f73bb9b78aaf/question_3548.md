# Q3548: NEAR normalized amount helpers rounding or denormalization misroutes value through cross-module drift

## Question
Can an unprivileged attacker use `public sign/finalize/claim paths across heterogeneous decimals` with control over amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches and desynchronize `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `rounding or denormalization misroutes value` attack class because normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Look for principal/fee recomputation from rounded destination amounts during claim or finalize callbacks. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz decimals and amount boundaries and assert that fee plus principal plus documented dust always equals the exact consumed source value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` and the adjacent the next module that consumes the same asset or transfer id after every branch.
