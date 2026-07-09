# Q2867: NEAR public transfer-message reads optional string alias changes bridge subject via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public off-chain coordination reads for pending transfers` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional string alias changes bridge subject` under returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on optional fee recipients, empty messages, token strings, and recipient strings. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-parse edge-case strings across all implementations and assert a single canonical meaning for every accepted value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
