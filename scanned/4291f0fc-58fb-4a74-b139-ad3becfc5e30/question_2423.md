# Q2423: NEAR provenance of `predecessor_account_id` final settlement and later fee claim can diverge through cross-module drift

## Question
Can an unprivileged attacker use `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` with control over cross-contract callback ordering, predecessor injection, and proof contents and desynchronize `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `final settlement and later fee claim can diverge` attack class because captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` and the adjacent storage billing and refund bookkeeping after every branch.
