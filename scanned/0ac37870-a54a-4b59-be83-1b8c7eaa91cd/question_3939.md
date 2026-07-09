# Q3939: NEAR send_fee_internal captured predecessor identity can be abused for fee payout via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public claim-fee and finalize callbacks through fee payout helper` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::send_fee_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `captured predecessor identity can be abused for fee payout` under routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type, violating `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
