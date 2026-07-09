# Q3286: NEAR storage account-id calculation storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `public outbound transfers that allow `external_id`` and make `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` release storage funds that still back unresolved bridge state because of derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers, violating `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
