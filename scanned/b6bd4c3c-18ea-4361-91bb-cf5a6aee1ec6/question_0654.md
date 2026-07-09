# Q654: NEAR public transfer-message reads origin and destination nonce desynchronization at boundary values

## Question
Can an unprivileged attacker trigger `public off-chain coordination reads for pending transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` violate `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer` in the `origin and destination nonce desynchronization` attack class because returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
