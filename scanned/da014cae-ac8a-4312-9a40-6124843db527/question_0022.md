# Q22: NEAR finish_withdraw_v2 origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `legacy/public withdrawal completion path on migrated tokens` with control over sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address and make `near/omni-bridge/src/lib.rs::finish_withdraw_v2` advance or reuse bridge nonces inconsistently with accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
