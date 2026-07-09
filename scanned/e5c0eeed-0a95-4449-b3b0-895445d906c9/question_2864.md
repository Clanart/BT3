# Q2864: NEAR remove_transfer_message flows callback refund creates value gap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public sign/finalize/claim callbacks through transfer cleanup` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback refund creates value gap` under cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
