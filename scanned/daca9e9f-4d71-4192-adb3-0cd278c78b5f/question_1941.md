# Q1941: NEAR send_fee_internal delivery callback leaves inconsistent state at boundary values

## Question
Can an unprivileged attacker trigger `public claim-fee and finalize callbacks through fee payout helper` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::send_fee_internal` violate `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket` in the `delivery callback leaves inconsistent state` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
