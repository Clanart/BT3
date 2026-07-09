# Q1287: NEAR fast-transfer storage encoding derived storage account can collide across transfers at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer claim and resolution flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` violate `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored` in the `derived storage account can collide across transfers` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
