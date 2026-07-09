# Q2107: NEAR storage account-id calculation derived storage account can collide across transfers

## Question
Can an unprivileged attacker use `public outbound transfers that allow `external_id`` to make `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` derive the same storage account or key for two distinct transfers because of derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers, violating `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt.
