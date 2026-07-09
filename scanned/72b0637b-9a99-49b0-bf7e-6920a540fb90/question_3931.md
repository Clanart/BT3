# Q3931: NEAR transfer message storage encoding final settlement and later fee claim can diverge via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public resume/sign/finalize paths that hash or persist transfer records` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` ends up accepting two inconsistent interpretations of the same economic event specifically around `final settlement and later fee claim can diverge` under serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
