# Q1643: NEAR provenance of `predecessor_account_id` storage-preparation omission changes settlement meaning via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage-preparation omission changes settlement meaning` under captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
