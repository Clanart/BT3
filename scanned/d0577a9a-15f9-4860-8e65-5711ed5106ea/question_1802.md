# Q1802: NEAR public transfer-message reads fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `public off-chain coordination reads for pending transfers` with control over transfer id choice and timing relative to sign/claim/finalize callbacks and desynchronize `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` and the adjacent replay-protection bookkeeping after every branch.
