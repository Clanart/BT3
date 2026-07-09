# Q2701: NEAR send_fee_internal fee recipient can be substituted or reclaimed by attacker

## Question
Can an unprivileged attacker use `public claim-fee and finalize callbacks through fee payout helper` to make `near/omni-bridge/src/lib.rs::send_fee_internal` route a legitimate fee to the wrong account because of routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type, violating `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee.
