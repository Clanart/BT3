# Q1852: NEAR storage_unregister refund goes to wrong logical owner at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_unregister` violate `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free` in the `refund goes to wrong logical owner` attack class because attempts to unregister an account from bridge storage accounting becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
