# Q1470: NEAR storage account-id calculation storage payer or owner spoofing

## Question
Can an unprivileged attacker cause `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` to bill, refund, or resume the wrong storage owner through `public outbound transfers that allow `external_id`` by abusing derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers, violating `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer.
