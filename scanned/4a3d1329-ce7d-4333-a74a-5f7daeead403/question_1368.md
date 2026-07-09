# Q1368: NEAR storage_withdraw refund goes to wrong logical owner

## Question
Can an unprivileged attacker exploit callbacks behind `public NEAR storage-management entrypoint` so that `near/omni-bridge/src/storage.rs::storage_withdraw` refunds storage to an account other than the one that actually funded the state because of subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage.
