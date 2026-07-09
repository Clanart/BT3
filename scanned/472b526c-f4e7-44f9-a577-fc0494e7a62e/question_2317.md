# Q2317: NEAR storage_unregister unregister can sever state that callbacks still need through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over force flag and timing relative to active pending/fast/finalized records and desynchronize `near/omni-bridge/src/storage.rs::storage_unregister` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `unregister can sever state that callbacks still need` attack class because attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_unregister` and the adjacent storage billing and refund bookkeeping after every branch.
