# Q1851: NEAR storage_withdraw refund goes to wrong logical owner at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_withdraw` violate `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds` in the `refund goes to wrong logical owner` attack class because subtracts from stored storage balance and transfers NEAR back to the caller becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
