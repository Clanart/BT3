# Q1480: NEAR public transfer-message reads fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public off-chain coordination reads for pending transfers` with crafted amount, fee, or native-fee inputs and make `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` use inconsistent fee and principal values across returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
