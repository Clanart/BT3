# Q2418: NEAR remove_transfer_message flows resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `public sign/finalize/claim callbacks through transfer cleanup` with control over transfer id, callback success/failure, storage owner, and removal order relative to payout or refund and desynchronize `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` and the adjacent replay-protection bookkeeping after every branch.
