# Q1477: NEAR remove_transfer_message flows storage payer or owner spoofing

## Question
Can an unprivileged attacker cause `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` to bill, refund, or resume the wrong storage owner through `public sign/finalize/claim callbacks through transfer cleanup` by abusing cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer.
