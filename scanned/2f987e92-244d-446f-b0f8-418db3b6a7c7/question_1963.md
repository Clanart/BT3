# Q1963: NEAR public transfer-message reads fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public off-chain coordination reads for pending transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` violate `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer` in the `fee and principal split divergence` attack class because returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
