# Q3414: NEAR send_fee_internal native fee and token fee drawn from wrong asset bucket via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public claim-fee and finalize callbacks through fee payout helper` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::send_fee_internal` ends up accepting two inconsistent interpretations of the same economic event specifically around `native fee and token fee drawn from wrong asset bucket` under routes fee payout between minting bridge tokens, transferring native tokens, or sending existing custody depending on origin and token type, violating `fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_fee_internal`
- Entrypoint: `public claim-fee and finalize callbacks through fee payout helper`
- Attacker controls: fee recipient, token id, token/ native fee split, and whether the asset is deployed or native
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fee payout helper logic must never pay the same fee twice or pay it from the wrong asset bucket
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
