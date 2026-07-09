# Q2012: NEAR storage_withdraw unregister can sever state that callbacks still need

## Question
Can an unprivileged attacker combine `public NEAR storage-management entrypoint` with later callbacks so that `near/omni-bridge/src/storage.rs::storage_withdraw` unregisters storage ownership before asynchronous cleanup runs, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely.
