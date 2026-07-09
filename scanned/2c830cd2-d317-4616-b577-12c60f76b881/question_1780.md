# Q1780: NEAR send_fee_internal delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `public claim-fee and finalize callbacks through fee payout helper` with control over fee recipient, token id, token/ native fee split, and whether the asset is deployed or native and desynchronize `near/omni-bridge/src/lib.rs::send_fee_internal` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type, violating `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::send_fee_internal` and the adjacent mint, burn, or custody accounting after every branch.
