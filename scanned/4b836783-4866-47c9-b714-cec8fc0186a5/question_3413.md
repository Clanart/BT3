# Q3413: NEAR normalized amount helpers rounding or denormalization misroutes value via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public sign/finalize/claim paths across heterogeneous decimals` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount` ends up accepting two inconsistent interpretations of the same economic event specifically around `rounding or denormalization misroutes value` under normalizes and denormalizes bridge amounts when crossing chains with different decimals and later uses those values for settlement and fee claim, violating `amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::normalize_amount/denormalize_amount`
- Entrypoint: `public sign/finalize/claim paths across heterogeneous decimals`
- Attacker controls: amount, fee, token decimals, origin decimals, destination decimals, and zero-fee versus nonzero-fee branches
- Exploit idea: Look for principal/fee recomputation from rounded destination amounts during claim or finalize callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: amount conversions must never misprice a user’s principal or fee by more than the documented dust behavior or let attackers move the rounding remainder to the wrong party
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz decimals and amount boundaries and assert that fee plus principal plus documented dust always equals the exact consumed source value. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
