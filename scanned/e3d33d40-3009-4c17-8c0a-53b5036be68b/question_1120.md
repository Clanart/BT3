# Q1120: NEAR transfer message storage encoding recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public resume/sign/finalize paths that hash or persist transfer records` with control over all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id and desynchronize `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` and the adjacent replay-protection bookkeeping after every branch.
