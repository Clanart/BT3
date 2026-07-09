# Q1026: NEAR finish_withdraw_v2 burn or lock before irreversible state through cross-module drift

## Question
Can an unprivileged attacker use `legacy/public withdrawal completion path on migrated tokens` with control over sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address and desynchronize `near/omni-bridge/src/lib.rs::finish_withdraw_v2` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `burn or lock before irreversible state` attack class because accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::finish_withdraw_v2` and the adjacent replay-protection bookkeeping after every branch.
