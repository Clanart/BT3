# Q1202: NEAR storage_deposit storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_deposit` violate `storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state` in the `storage withdrawal escapes live liabilities` attack class because tracks storage balances per account and is used by bridge paths that bill users and relayers for pending transfer records becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_deposit`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: account id to credit, attached deposit, and timing around pending transfers or yields
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
