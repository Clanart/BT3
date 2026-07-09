# Q955: NEAR fast-transfer storage encoding derived storage account can collide across transfers via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer claim and resolution flows` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` ends up accepting two inconsistent interpretations of the same economic event specifically around `derived storage account can collide across transfers` under deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
