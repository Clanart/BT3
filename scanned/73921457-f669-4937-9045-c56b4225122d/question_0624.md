# Q624: NEAR message-account funding helper recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public init-transfer and resume flows through message-storage payment` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` violate `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another` in the `recipient or message ambiguity` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
