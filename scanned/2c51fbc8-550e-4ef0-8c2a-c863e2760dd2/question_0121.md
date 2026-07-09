# Q121: NEAR promise bookkeeping origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public yield-resume flow through deferred outbound transfers` with control over message-storage account id, yielded promise id, repeated funding, and callback timing and make `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` advance or reuse bridge nonces inconsistently with tracks deferred init-transfer promises by account id so they can resume once storage arrives, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
