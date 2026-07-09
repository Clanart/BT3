# Q3287: NEAR transfer-id/unified-id mixing relayer substitution changes economic recipient

## Question
Can an unprivileged attacker exploit `public fast-transfer and UTXO branches` so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` redirects principal or fee to a relayer under conditions that do not match the original user transfer, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout.
