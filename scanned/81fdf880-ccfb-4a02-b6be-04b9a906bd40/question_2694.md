# Q2694: NEAR message-account funding helper derived storage account can collide across transfers

## Question
Can an unprivileged attacker use `public init-transfer and resume flows through message-storage payment` to make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` derive the same storage account or key for two distinct transfers because of moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized, violating `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt.
