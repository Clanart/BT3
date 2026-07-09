# Q789: NEAR fast-transfer storage encoding derived storage account can collide across transfers

## Question
Can an unprivileged attacker use `public fast-transfer claim and resolution flows` to make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` derive the same storage account or key for two distinct transfers because of deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt.
