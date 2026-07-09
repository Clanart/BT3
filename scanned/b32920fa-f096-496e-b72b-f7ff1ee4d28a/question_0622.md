# Q622: NEAR fast-transfer storage encoding fast-transfer storage refund reaches wrong party at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer claim and resolution flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` violate `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored` in the `fast-transfer storage refund reaches wrong party` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
