# Q2013: NEAR storage_unregister unregister can sever state that callbacks still need

## Question
Can an unprivileged attacker combine `public NEAR storage-management entrypoint` with later callbacks so that `near/omni-bridge/src/storage.rs::storage_unregister` unregisters storage ownership before asynchronous cleanup runs, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely.
