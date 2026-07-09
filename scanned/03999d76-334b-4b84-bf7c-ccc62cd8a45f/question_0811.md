# Q811: NEAR transfer-id/unified-id mixing replay state keyed too narrowly for the true domain

## Question
Can an unprivileged attacker exploit `public fast-transfer and UTXO branches` so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` treats two events from different chains, assets, or message classes as sharing one replay slot because of mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly.
