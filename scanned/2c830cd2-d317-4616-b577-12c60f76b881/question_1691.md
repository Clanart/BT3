# Q1691: NEAR storage_unregister refund goes to wrong logical owner through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over force flag and timing relative to active pending/fast/finalized records and desynchronize `near/omni-bridge/src/storage.rs::storage_unregister` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `refund goes to wrong logical owner` attack class because attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_unregister` and the adjacent storage billing and refund bookkeeping after every branch.
