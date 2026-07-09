# Q3556: NEAR storage account-id calculation storage withdrawal escapes live liabilities through cross-module drift

## Question
Can an unprivileged attacker use `public outbound transfers that allow `external_id`` with control over `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id and desynchronize `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage withdrawal escapes live liabilities` attack class because derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers, violating `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` and the adjacent replay-protection bookkeeping after every branch.
