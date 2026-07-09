# Q3747: NEAR storage_unregister different callback outcomes produce the same user-visible success

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` so that `near/omni-bridge/src/storage.rs::storage_unregister` treats materially different callback outcomes as the same economic result because of attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition.
