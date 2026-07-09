# Q1289: NEAR message-account funding helper fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public init-transfer and resume flows through message-storage payment` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` violate `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another` in the `fee and principal split divergence` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
