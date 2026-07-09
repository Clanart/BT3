# Q4: NEAR init_transfer resume path origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `yield-resume callback for a previously deferred outbound transfer` with control over timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents and make `near/omni-bridge/src/lib.rs::init_transfer_resume` advance or reuse bridge nonces inconsistently with removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
