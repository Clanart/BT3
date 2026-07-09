# Q3478: NEAR storage_unregister callback interprets missing storage check as success through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over force flag and timing relative to active pending/fast/finalized records and desynchronize `near/omni-bridge/src/storage.rs::storage_unregister` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `callback interprets missing storage check as success` attack class because attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_unregister` and the adjacent storage billing and refund bookkeeping after every branch.
