# Q986: NEAR public transfer-message reads recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public off-chain coordination reads for pending transfers` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under returns stored pending-transfer records that off-chain actors use to build signatures, fees, and proofs, violating `publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_transfer_message / get_transfer_message_storage`
- Entrypoint: `public off-chain coordination reads for pending transfers`
- Attacker controls: transfer id choice and timing relative to sign/claim/finalize callbacks
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: publicly-observable pending state must remain canonical so external actors cannot be induced to sign or relay the wrong transfer
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
