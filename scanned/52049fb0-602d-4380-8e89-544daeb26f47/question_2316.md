# Q2316: NEAR storage_withdraw unregister can sever state that callbacks still need through cross-module drift

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` with control over withdraw amount, caller account, and timing relative to pending transfer lifecycle and desynchronize `near/omni-bridge/src/storage.rs::storage_withdraw` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `unregister can sever state that callbacks still need` attack class because subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::storage_withdraw` and the adjacent storage billing and refund bookkeeping after every branch.
