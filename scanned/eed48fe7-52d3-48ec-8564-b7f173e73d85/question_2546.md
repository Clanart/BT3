# Q2546: NEAR message-account funding helper fee payout and storage refund overlap at boundary values

## Question
Can an unprivileged attacker trigger `public init-transfer and resume flows through message-storage payment` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account` violate `message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another` in the `fee payout and storage refund overlap` attack class because moves NEAR from a calculated message account into a storage owner account before outbound transfer state is finalized becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::try_to_transfer_balance_from_message_account`
- Entrypoint: `public init-transfer and resume flows through message-storage payment`
- Attacker controls: message-storage account id, signer id, required storage, native fee, and transfer timing
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: message-account funding must not be spoofable or replayable in a way that funds one transfer while authorizing another
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
