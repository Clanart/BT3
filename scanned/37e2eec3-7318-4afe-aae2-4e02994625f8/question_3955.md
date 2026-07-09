# Q3955: NEAR provenance of `predecessor_account_id` captured predecessor identity can be abused for fee payout via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` ends up accepting two inconsistent interpretations of the same economic event specifically around `captured predecessor identity can be abused for fee payout` under captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Attack callbacks that carry caller identity as an argument across promises instead of re-reading a trusted on-chain source. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Try nested calls and intermediary contracts and assert that callback arguments cannot redirect fee entitlement away from the authentic proof subject. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
