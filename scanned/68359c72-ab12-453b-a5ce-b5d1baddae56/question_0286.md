# Q286: NEAR fast-transfer storage encoding fast-transfer storage refund reaches wrong party via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer claim and resolution flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer storage refund reaches wrong party` under deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target stored `storage_owner` values and removal paths that issue refunds after relayer activity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Create relayer and user combinations and assert that every refund lands on the exact payer who financed that fast-transfer slot. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
