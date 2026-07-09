# Q631: NEAR send_fee_internal recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public claim-fee and finalize callbacks through fee payout helper` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::send_fee_internal` violate `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket` in the `recipient or fee-recipient rebinding` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
