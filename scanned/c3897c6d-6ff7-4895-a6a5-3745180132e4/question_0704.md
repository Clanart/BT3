# Q704: NEAR storage_deposit storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `public NEAR storage-management entrypoint` and make `near/omni-bridge/src/storage.rs::storage_deposit` release storage funds that still back unresolved bridge state because of tracks storage balances per account and is used by bridge paths that bill users and relayers for pending transfer records, violating `storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_deposit`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: account id to credit, attached deposit, and timing around pending transfers or yields
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: storage balances must never be overstated, transferable twice, or withdrawable while still backing live bridge state
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
