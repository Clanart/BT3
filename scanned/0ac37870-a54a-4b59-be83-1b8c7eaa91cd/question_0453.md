# Q453: NEAR transfer message storage encoding origin and destination nonce desynchronization through cross-module drift

## Question
Can an unprivileged attacker use `public resume/sign/finalize paths that hash or persist transfer records` with control over all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id and desynchronize `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `origin and destination nonce desynchronization` attack class because serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` and the adjacent replay-protection bookkeeping after every branch.
