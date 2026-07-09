# Q1192: NEAR finish_withdraw_v2 burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `legacy/public withdrawal completion path on migrated tokens` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::finish_withdraw_v2` violate `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient` in the `burn or lock before irreversible state` attack class because accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
