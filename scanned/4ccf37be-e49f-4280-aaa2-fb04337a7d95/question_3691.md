# Q3691: NEAR storage account-id calculation storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public outbound transfers that allow `external_id`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` violate `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer` in the `storage withdrawal escapes live liabilities` attack class because derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
