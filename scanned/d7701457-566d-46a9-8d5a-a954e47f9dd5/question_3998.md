# Q3998: NEAR storage_withdraw different callback outcomes produce the same user-visible success through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over withdraw amount, caller account, and timing relative to pending transfer lifecycle and desynchronize `near/omni-bridge/src/storage.rs::storage_withdraw` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `different callback outcomes produce the same user-visible success` attack class because subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_withdraw` and the adjacent storage billing and refund bookkeeping after every branch.
