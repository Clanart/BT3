# Q2271: NEAR provenance of `predecessor_account_id` final settlement and later fee claim can diverge via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` ends up accepting two inconsistent interpretations of the same economic event specifically around `final settlement and later fee claim can diverge` under captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
