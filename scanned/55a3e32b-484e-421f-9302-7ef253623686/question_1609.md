# Q1609: NEAR transfer message storage encoding fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public resume/sign/finalize paths that hash or persist transfer records` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
