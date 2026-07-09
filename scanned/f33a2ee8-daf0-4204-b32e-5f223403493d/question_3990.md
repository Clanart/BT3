# Q3990: NEAR finish_withdraw_v2 numeric cast or overflow changes economic meaning through cross-module drift

## Question
Can an unprivileged attacker use `legacy/public withdrawal completion path on migrated tokens` with control over sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address and desynchronize `near/omni-bridge/src/lib.rs::finish_withdraw_v2` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `numeric cast or overflow changes economic meaning` attack class because accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::finish_withdraw_v2` and the adjacent replay-protection bookkeeping after every branch.
