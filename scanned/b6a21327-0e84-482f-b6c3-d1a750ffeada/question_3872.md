# Q3872: NEAR storage_withdraw different callback outcomes produce the same user-visible success via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public NEAR storage-management entrypoint` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/storage.rs::storage_withdraw` ends up accepting two inconsistent interpretations of the same economic event specifically around `different callback outcomes produce the same user-visible success` under subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
