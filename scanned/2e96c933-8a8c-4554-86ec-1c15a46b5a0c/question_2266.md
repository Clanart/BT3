# Q2266: NEAR remove_transfer_message flows resume-path replay or duplication via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public sign/finalize/claim callbacks through transfer cleanup` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` ends up accepting two inconsistent interpretations of the same economic event specifically around `resume-path replay or duplication` under cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
