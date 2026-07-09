# Q2553: NEAR send_fee_internal final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public claim-fee and finalize callbacks through fee payout helper` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::send_fee_internal` violate `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket` in the `final settlement and later fee claim can diverge` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
