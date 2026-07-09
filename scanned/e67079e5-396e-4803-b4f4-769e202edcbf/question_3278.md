# Q3278: NEAR normalized amount helpers rounding or denormalization misroutes value

## Question
Can an unprivileged attacker craft edge amounts through `public sign/finalize/claim paths across heterogeneous decimals` that make `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` assign principal and fee inconsistently across normalization or denormalization steps beyond the documented dust behavior, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Look for principal/fee recomputation from rounded destination amounts during claim or finalize callbacks.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz decimals and amount boundaries and assert that fee plus principal plus documented dust always equals the exact consumed source value.
