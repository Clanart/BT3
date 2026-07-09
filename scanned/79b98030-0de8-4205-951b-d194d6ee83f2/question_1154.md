# Q1154: NEAR provenance of `predecessor_account_id` delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` with control over cross-contract callback ordering, predecessor injection, and proof contents and desynchronize `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` and the adjacent storage billing and refund bookkeeping after every branch.
