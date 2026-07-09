# Q1286: NEAR transfer message storage encoding recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public resume/sign/finalize paths that hash or persist transfer records` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` violate `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id` in the `recipient or message ambiguity` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
