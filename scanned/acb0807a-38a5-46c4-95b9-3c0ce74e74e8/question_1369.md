# Q1369: NEAR storage_unregister refund goes to wrong logical owner

## Question
Can an unprivileged attacker exploit callbacks behind `public NEAR storage-management entrypoint` so that `near/omni-bridge/src/storage.rs::storage_unregister` refunds storage to an account other than the one that actually funded the state because of attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage.
