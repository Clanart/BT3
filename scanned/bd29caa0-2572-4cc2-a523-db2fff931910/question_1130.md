# Q1130: NEAR send_fee_internal asset-branch confusion on finalization through cross-module drift

## Question
Can an unprivileged attacker use `public claim-fee and finalize callbacks through fee payout helper` with control over fee recipient, token id, token/ native fee split, and whether the asset is deployed or native and desynchronize `near/omni-bridge/src/lib.rs::send_fee_internal` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `asset-branch confusion on finalization` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type, violating `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_fee_internal` and the adjacent mint, burn, or custody accounting after every branch.
