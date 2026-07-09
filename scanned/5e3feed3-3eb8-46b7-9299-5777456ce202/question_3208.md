# Q3208: NEAR storage_unregister callback interprets missing storage check as success

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` to make `near/omni-bridge/src/storage.rs::storage_unregister` misread asynchronous storage-check results because of attempts to unregister an account from bridge storage accounting, violating `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout.
