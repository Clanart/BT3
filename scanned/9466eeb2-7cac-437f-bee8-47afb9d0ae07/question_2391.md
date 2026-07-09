# Q2391: NEAR transfer message storage encoding replay guard can be bypassed or consumed incorrectly through cross-module drift

## Question
Can an unprivileged attacker use `public resume/sign/finalize paths that hash or persist transfer records` with control over all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id and desynchronize `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay guard can be bypassed or consumed incorrectly` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` and the adjacent replay-protection bookkeeping after every branch.
