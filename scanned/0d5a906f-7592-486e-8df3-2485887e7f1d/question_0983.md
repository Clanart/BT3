# Q983: NEAR remove_transfer_message flows fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public sign/finalize/claim callbacks through transfer cleanup` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
