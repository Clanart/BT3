# Q1690: NEAR storage_withdraw refund goes to wrong logical owner through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over withdraw amount, caller account, and timing relative to pending transfer lifecycle and desynchronize `near/omni-bridge/src/storage.rs::storage_withdraw` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `refund goes to wrong logical owner` attack class because subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_withdraw` and the adjacent storage billing and refund bookkeeping after every branch.
