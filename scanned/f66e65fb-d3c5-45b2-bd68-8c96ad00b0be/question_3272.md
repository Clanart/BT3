# Q3272: NEAR message-account funding helper storage quote underestimates live state

## Question
Can an unprivileged attacker reach `public init-transfer and resume flows through message-storage payment` and make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` reserve less storage than the live bridge state actually consumes because of moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized, violating `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
