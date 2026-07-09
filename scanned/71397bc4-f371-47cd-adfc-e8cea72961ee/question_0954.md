# Q954: NEAR transfer message storage encoding recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public resume/sign/finalize paths that hash or persist transfer records` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
