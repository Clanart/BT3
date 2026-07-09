# Q2691: NEAR transfer message storage encoding recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public resume/sign/finalize paths that hash or persist transfer records` that makes `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` settle principal to one party but authorize fee claim or callback routing for another due to serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
