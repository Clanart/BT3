# Q150: NEAR public transfer-message reads origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public off-chain coordination reads for pending transfers` with control over transfer id choice and timing relative to sign/claim/finalize callbacks and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` advance or reuse bridge nonces inconsistently with returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
