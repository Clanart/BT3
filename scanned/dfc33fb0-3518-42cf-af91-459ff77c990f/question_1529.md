# Q1529: NEAR storage_withdraw refund goes to wrong logical owner via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public NEAR storage-management entrypoint` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/storage.rs::storage_withdraw` ends up accepting two inconsistent interpretations of the same economic event specifically around `refund goes to wrong logical owner` under subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
