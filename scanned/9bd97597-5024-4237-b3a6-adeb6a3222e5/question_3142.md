# Q3142: NEAR send_fee_internal fee recipient can be substituted or reclaimed by attacker at boundary values

## Question
Can an unprivileged attacker trigger `public claim-fee and finalize callbacks through fee payout helper` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::send_fee_internal` violate `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket` in the `fee recipient can be substituted or reclaimed by attacker` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
