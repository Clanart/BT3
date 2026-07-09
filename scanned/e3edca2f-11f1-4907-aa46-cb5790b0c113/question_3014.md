# Q3014: NEAR public transfer-message reads optional string alias changes bridge subject through cross-module drift

## Question
Can an unprivileged attacker use `public off-chain coordination reads for pending transfers` with control over transfer id choice and timing relative to sign/claim/finalize callbacks and desynchronize `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `optional string alias changes bridge subject` attack class because returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on optional fee recipients, empty messages, token strings, and recipient strings. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-parse edge-case strings across all implementations and assert a single canonical meaning for every accepted value. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` and the adjacent replay-protection bookkeeping after every branch.
