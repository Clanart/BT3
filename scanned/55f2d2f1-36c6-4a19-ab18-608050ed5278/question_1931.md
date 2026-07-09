# Q1931: NEAR transfer message storage encoding fee and principal split divergence at boundary values

## Question
Can an unprivileged attacker trigger `public resume/sign/finalize paths that hash or persist transfer records` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` violate `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id` in the `fee and principal split divergence` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
