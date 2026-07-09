# Q621: NEAR transfer message storage encoding origin and destination nonce desynchronization at boundary values

## Question
Can an unprivileged attacker trigger `public resume/sign/finalize paths that hash or persist transfer records` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` violate `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id` in the `origin and destination nonce desynchronization` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
