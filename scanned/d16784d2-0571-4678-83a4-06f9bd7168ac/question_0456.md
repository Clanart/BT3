# Q456: NEAR message-account funding helper recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public init-transfer and resume flows through message-storage payment` with control over message-storage account id, signer id, required storage, native fee, and transfer timing and desynchronize `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized, violating `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` and the adjacent storage billing and refund bookkeeping after every branch.
