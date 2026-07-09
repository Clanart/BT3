# Q1934: NEAR message-account funding helper storage payer or owner spoofing at boundary values

## Question
Can an unprivileged attacker trigger `public init-transfer and resume flows through message-storage payment` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` violate `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another` in the `storage payer or owner spoofing` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
