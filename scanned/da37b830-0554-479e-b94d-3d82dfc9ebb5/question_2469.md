# Q2469: NEAR storage_unregister unregister can sever state that callbacks still need at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_unregister` violate `unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free` in the `unregister can sever state that callbacks still need` attack class because attempts to unregister an account from bridge storage accounting becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_unregister`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: force flag and timing relative to active pending/fast/finalized records
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: unregister must never let an attacker sever the balance record that still backs unresolved bridge state or unlock more NEAR than was truly free
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
