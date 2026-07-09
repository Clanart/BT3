# Q1121: NEAR fast-transfer storage encoding derived storage account can collide across transfers through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer claim and resolution flows` with control over fast-transfer id fields, relayer id, finalised flag, and storage owner and desynchronize `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `derived storage account can collide across transfers` attack class because deserializes relayer-sponsored fast-transfer state that later decides payout redirection and fee-claim eligibility, violating `fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main`
- Entrypoint: `public fast-transfer claim and resolution flows`
- Attacker controls: fast-transfer id fields, relayer id, finalised flag, and storage owner
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: fast-transfer state deserialization must not let an attacker recover a different relayer, status, or storage owner than the bridge originally stored
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::FastTransferStatusStorage::into_main` and the adjacent storage billing and refund bookkeeping after every branch.
