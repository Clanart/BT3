# Q118: NEAR fast-transfer storage encoding fast-transfer storage refund reaches wrong party

## Question
Can an unprivileged attacker exploit `public fast-transfer claim and resolution flows` so that `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` refunds reserved fast-transfer storage to the wrong account because of deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot.
