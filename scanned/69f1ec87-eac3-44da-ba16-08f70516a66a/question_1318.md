# Q1318: NEAR public transfer-message reads recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public off-chain coordination reads for pending transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` violate `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer` in the `recipient or message ambiguity` attack class because returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
