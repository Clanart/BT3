# Q146: NEAR remove_transfer_message flows origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public sign/finalize/claim callbacks through transfer cleanup` with control over transfer id, callback success/failure, storage owner, and removal order relative to payout or refund and make `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund` advance or reuse bridge nonces inconsistently with cleans up pending-transfer state after signing, fee claim, callback refunds, or finalization failure, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::remove_transfer_message/remove_fin_transfer/remove_transfer_message_without_refund`
- Entrypoint: `public sign/finalize/claim callbacks through transfer cleanup`
- Attacker controls: transfer id, callback success/failure, storage owner, and removal order relative to payout or refund
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: cleanup must never reopen replay protection or double-refund storage while the economic effect of the transfer already escaped
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
