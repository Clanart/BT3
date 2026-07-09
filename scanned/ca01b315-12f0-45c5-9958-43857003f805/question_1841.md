# Q1841: NEAR finish_withdraw_v2 recipient or message ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public withdrawal completion path on migrated tokens` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::finish_withdraw_v2` violate `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient` in the `recipient or message ambiguity` attack class because accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
