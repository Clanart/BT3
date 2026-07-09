# Q2165: NEAR storage_unregister unregister can sever state that callbacks still need via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public NEAR storage-management entrypoint` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/storage.rs::storage_unregister` ends up accepting two inconsistent interpretations of the same economic event specifically around `unregister can sever state that callbacks still need` under attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
