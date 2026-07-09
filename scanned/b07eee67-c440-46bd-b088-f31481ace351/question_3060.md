# Q3060: NEAR storage_withdraw promise bookkeeping can be overwritten or orphaned at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_withdraw` violate `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds` in the `promise bookkeeping can be overwritten or orphaned` attack class because subtracts from stored storage balance and transfers NEAR back to the caller becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Probe maps keyed only by account ids or derived storage accounts when multiple pending operations are possible. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Create overlapping deferred operations and assert that each one has independent bookkeeping and cleanup. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
