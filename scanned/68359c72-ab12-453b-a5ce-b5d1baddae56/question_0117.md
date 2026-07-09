# Q117: NEAR transfer message storage encoding origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public resume/sign/finalize paths that hash or persist transfer records` with control over all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id and make `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh` advance or reuse bridge nonces inconsistently with serializes pending transfer records that later feed signing, resume, fee claim, and settlement logic, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::TransferMessageStorage::encode_borsh`
- Entrypoint: `public resume/sign/finalize paths that hash or persist transfer records`
- Attacker controls: all stored transfer fields including origin nonce, destination nonce, fee, sender, recipient, and optional origin transfer id
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: persisted transfer bytes must stay canonical so stored state cannot be reinterpreted under another message hash or transfer id
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
