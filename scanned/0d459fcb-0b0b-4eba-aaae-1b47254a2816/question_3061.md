# Q3061: NEAR storage_unregister promise bookkeeping can be overwritten or orphaned at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_unregister` violate `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free` in the `promise bookkeeping can be overwritten or orphaned` attack class because attempts to unregister an account from bridge storage accounting becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Probe maps keyed only by account ids or derived storage accounts when multiple pending operations are possible. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Create overlapping deferred operations and assert that each one has independent bookkeeping and cleanup. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
