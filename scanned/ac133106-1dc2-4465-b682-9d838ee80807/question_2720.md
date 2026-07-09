# Q2720: NEAR public transfer-message reads optional string alias changes bridge subject

## Question
Can an unprivileged attacker use empty, null, or specially-encoded strings in `public off-chain coordination reads for pending transfers` such that `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` treats them as one semantic subject while another parser treats them as another, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Focus on optional fee recipients, empty messages, token strings, and recipient strings.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-parse edge-case strings across all implementations and assert a single canonical meaning for every accepted value.
