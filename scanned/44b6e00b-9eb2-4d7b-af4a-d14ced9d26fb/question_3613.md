# Q3613: NEAR storage_unregister callback interprets missing storage check as success at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_unregister` violate `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free` in the `callback interprets missing storage check as success` attack class because attempts to unregister an account from bridge storage accounting becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
