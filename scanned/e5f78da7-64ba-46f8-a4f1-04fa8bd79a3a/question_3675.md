# Q3675: NEAR transfer message storage encoding one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `public resume/sign/finalize paths that hash or persist transfer records` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` violate `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id` in the `one inbound event spawns multiple outbound obligations` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
