# Q643: NEAR storage account-id calculation origin and destination nonce desynchronization at boundary values

## Question
Can an unprivileged attacker trigger `public outbound transfers that allow `external_id`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers` violate `storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer` in the `origin and destination nonce desynchronization` attack class because derives a per-transfer storage account id that can be pre-funded and later used to resume deferred transfers becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::calculate_storage_account_id and init-transfer callers`
- Entrypoint: `public outbound transfers that allow `external_id``
- Attacker controls: `external_id`, sender, recipient, token, origin nonce, and any fields used to derive the message-storage account id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: storage account ids must be collision-resistant across attacker-controlled `external_id` choices so one user cannot fund or resume another user’s transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
