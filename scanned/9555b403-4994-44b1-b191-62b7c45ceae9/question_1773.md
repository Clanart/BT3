# Q1773: NEAR message-account funding helper storage payer or owner spoofing through cross-module drift

## Question
Can an unprivileged attacker use `public init-transfer and resume flows through message-storage payment` with control over message-storage account id, signer id, required storage, native fee, and transfer timing and desynchronize `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage payer or owner spoofing` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized, violating `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Exploit signer/predecessor splits, message-storage account ids, or promise bookkeeping to shift storage liabilities between accounts. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate conflicting `sender_id`, `signer_id`, and pre-funded storage accounts and assert that only the intended payer can fund, resume, or recover that transfer. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` and the adjacent storage billing and refund bookkeeping after every branch.
