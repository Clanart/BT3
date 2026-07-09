# Q289: NEAR promise bookkeeping origin and destination nonce desynchronization via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public yield-resume flow through deferred outbound transfers` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` ends up accepting two inconsistent interpretations of the same economic event specifically around `origin and destination nonce desynchronization` under tracks deferred init-transfer promises by account id so they can resume once storage arrives, violating `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
