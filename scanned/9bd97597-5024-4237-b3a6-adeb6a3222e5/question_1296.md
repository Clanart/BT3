# Q1296: NEAR send_fee_internal asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `public claim-fee and finalize callbacks through fee payout helper` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::send_fee_internal` violate `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket` in the `asset-branch confusion on finalization` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
