# Q488: NEAR provenance of `predecessor_account_id` recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument` with control over cross-contract callback ordering, predecessor injection, and proof contents and desynchronize `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because captures `env::predecessor_account_id()` before asynchronous calls and later trusts the carried value for payout routing or storage billing, violating `asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account`?

## Target
- File/function: `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly`
- Entrypoint: `public finalize and fee-claim flows whose callbacks receive predecessor identity as an argument`
- Attacker controls: cross-contract callback ordering, predecessor injection, and proof contents
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: asynchronous identity capture must not let attackers cause one caller’s proof or fee claim to be settled under another caller’s authority or storage account
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs callbacks that carry `predecessor_account_id` explicitly` and the adjacent storage billing and refund bookkeeping after every branch.
