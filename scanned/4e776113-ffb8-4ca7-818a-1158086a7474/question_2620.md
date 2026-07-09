# Q2620: NEAR storage_unregister promise bookkeeping can be overwritten or orphaned

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` to overwrite or strand another deferred operation inside `near/omni-bridge/src/storage.rs::storage_unregister` because of attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Probe maps keyed only by account ids or derived storage accounts when multiple pending operations are possible.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Create overlapping deferred operations and assert that each one has independent bookkeeping and cleanup.
