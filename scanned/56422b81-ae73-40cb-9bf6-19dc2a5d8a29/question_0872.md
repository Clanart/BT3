# Q872: NEAR storage_unregister storage withdrawal escapes live liabilities via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public NEAR storage-management entrypoint` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/storage.rs::storage_unregister` ends up accepting two inconsistent interpretations of the same economic event specifically around `storage withdrawal escapes live liabilities` under attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
