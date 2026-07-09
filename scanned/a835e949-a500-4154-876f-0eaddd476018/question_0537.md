# Q537: NEAR storage_deposit storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_deposit` violate `storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state` in the `storage quote underestimates live state` attack class because tracks storage balances per account and is used by bridge paths that bill users and relayers for pending transfer records becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_deposit`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: account id to credit, attached deposit, and timing around pending transfers or yields
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
